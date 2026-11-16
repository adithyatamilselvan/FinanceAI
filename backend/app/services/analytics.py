"""Single source of truth for financial definitions. Every figure shown anywhere is built from these functions.

Definitions (all in integer minor units; transfers are always excluded):
  income        = sum(type == 'income')
  gross_expense = sum(type == 'expense')
  refunds       = sum(type == 'refund')           (a refund/reversal offsets spending)
  net_expense   = gross_expense - refunds
  net_cash_flow = income - net_expense            (== monthly savings)
  savings_pct   = net_cash_flow / income * 100    (None when income is 0)
"""
from datetime import date, datetime, timedelta, timezone

from ..money import pct
from ..util import add_months, month_bounds, month_key, months_between
from . import categorizer as cz


def category_meta(db, uid) -> dict[str, dict]:
    """Per-category flags (fixed/essential/type) merged from built-ins and the user's custom categories."""
    meta: dict[str, dict] = {}
    for c in cz.EXPENSE_CATEGORIES:
        meta[c] = {"type": "expense", "fixed": c in cz.FIXED_CATEGORIES, "essential": c in cz.ESSENTIAL_CATEGORIES, "custom": False}
    for c in cz.INCOME_CATEGORIES:
        meta[c] = {"type": "income", "fixed": False, "essential": False, "custom": False}
    for d in db.categories.find({"user_id": uid, "kind": "category"}):
        meta[d["name"]] = {"type": d["type"], "fixed": bool(d.get("fixed")), "essential": bool(d.get("essential")), "custom": True}
    return meta


def _range_match(uid, start: datetime | None, end: datetime | None) -> dict:
    m: dict = {"user_id": uid, "type": {"$in": ["income", "expense", "refund"]}}
    if start or end:
        m["date"] = {}
        if start:
            m["date"]["$gte"] = start
        if end:
            m["date"]["$lt"] = end
    return m


def period_totals(db, uid, start: datetime | None, end: datetime | None) -> dict:
    rows = db.transactions.aggregate([
        {"$match": _range_match(uid, start, end)},
        {"$group": {"_id": {"t": "$type", "c": "$category"}, "total": {"$sum": "$amount"}, "n": {"$sum": 1}}},
    ])
    income = gross = refunds = n = 0
    by_cat: dict[str, int] = {}
    income_by_cat: dict[str, int] = {}
    for r in rows:
        t, c, total = r["_id"]["t"], r["_id"]["c"], int(r["total"])
        n += r["n"]
        if t == "income":
            income += total
            income_by_cat[c] = income_by_cat.get(c, 0) + total
        elif t == "expense":
            gross += total
            by_cat[c] = by_cat.get(c, 0) + total
        else:
            refunds += total
            by_cat[c] = by_cat.get(c, 0) - total
    net_expense = gross - refunds
    flow = income - net_expense
    return {
        "income": income, "gross_expense": gross, "refunds": refunds, "net_expense": net_expense,
        "net_cash_flow": flow, "savings": flow, "savings_pct": pct(flow, income),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "income_by_category": income_by_cat, "transaction_count": n,
    }


def month_totals(db, uid, period: str) -> dict:
    s, e = month_bounds(period)
    out = period_totals(db, uid, s, e)
    out["period"] = period
    return out


def monthly_series(db, uid, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    rows = db.transactions.aggregate([
        {"$match": _range_match(uid, start, end)},
        {"$group": {"_id": {"m": {"$dateToString": {"format": "%Y-%m", "date": "$date"}}, "t": "$type"}, "total": {"$sum": "$amount"}}},
    ])
    acc: dict[str, dict[str, int]] = {}
    for r in rows:
        d = acc.setdefault(r["_id"]["m"], {"income": 0, "expense": 0, "refund": 0})
        d[r["_id"]["t"]] += int(r["total"])
    out = []
    for m in sorted(acc):
        d = acc[m]
        net = d["expense"] - d["refund"]
        out.append({"period": m, "income": d["income"], "net_expense": net, "net_cash_flow": d["income"] - net})
    return out


def monthly_category_series(db, uid, start: datetime | None = None, end: datetime | None = None) -> dict[str, dict[str, int]]:
    """{period: {category: net_expense}} for expense/refund transactions."""
    m = _range_match(uid, start, end)
    m["type"] = {"$in": ["expense", "refund"]}
    rows = db.transactions.aggregate([
        {"$match": m},
        {"$group": {"_id": {"m": {"$dateToString": {"format": "%Y-%m", "date": "$date"}}, "c": "$category", "t": "$type"}, "total": {"$sum": "$amount"}}},
    ])
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        sign = 1 if r["_id"]["t"] == "expense" else -1
        d = out.setdefault(r["_id"]["m"], {})
        d[r["_id"]["c"]] = d.get(r["_id"]["c"], 0) + sign * int(r["total"])
    return out


def latest_data_period(db, uid) -> str | None:
    doc = db.transactions.find_one({"user_id": uid, "type": {"$in": ["income", "expense", "refund"]}}, sort=[("date", -1)], projection={"date": 1})
    return month_key(doc["date"]) if doc else None


def default_period(db, uid, today: date | None = None) -> str:
    """Latest month that has transactions, else the current month."""
    return latest_data_period(db, uid) or month_key(today or datetime.now(timezone.utc).date())


def completed_month_history(db, uid, today: date | None = None, max_months: int = 24) -> list[dict]:
    """Monthly series for complete months only, ending at the latest month with data that is before the current month.

    Anchoring on the user's own data (not the calendar) means an older imported statement still produces history.
    """
    today = today or datetime.now(timezone.utc).date()
    prev = add_months(month_key(today), -1)
    latest = latest_data_period(db, uid)
    if latest is None:
        return []
    anchor = min(latest, prev)
    first = month_bounds(add_months(anchor, -(max_months - 1)))[0]
    end = month_bounds(anchor)[1]
    return monthly_series(db, uid, first, end)


def fixed_variable_split(by_category: dict[str, int], meta: dict[str, dict]) -> dict:
    fixed = sum(v for c, v in by_category.items() if meta.get(c, {}).get("fixed"))
    total = sum(by_category.values())
    return {"fixed": fixed, "variable": total - fixed, "fixed_pct": pct(fixed, total)}


def comparison(cur: dict, prev: dict | None) -> dict | None:
    if prev is None or (prev["income"] == 0 and prev["net_expense"] == 0):
        return None
    return {
        "income_change": cur["income"] - prev["income"],
        "expense_change": cur["net_expense"] - prev["net_expense"],
        "savings_change": cur["net_cash_flow"] - prev["net_cash_flow"],
        "expense_change_pct": pct(cur["net_expense"] - prev["net_expense"], prev["net_expense"]),
        "income_change_pct": pct(cur["income"] - prev["income"], prev["income"]),
        "previous": {"income": prev["income"], "net_expense": prev["net_expense"], "net_cash_flow": prev["net_cash_flow"]},
    }


def dashboard(db, uid, period: str | None = None, today: date | None = None) -> dict:
    period = period or default_period(db, uid, today)
    meta = category_meta(db, uid)
    cur = month_totals(db, uid, period)
    prev = month_totals(db, uid, add_months(period, -1))
    yoy = month_totals(db, uid, add_months(period, -12))
    s, _ = month_bounds(add_months(period, -5))
    _, e = month_bounds(period)
    trend = monthly_series(db, uid, s, e)
    has_any = db.transactions.count_documents({"user_id": uid}, limit=1) > 0
    return {
        "period": period,
        "has_transactions": has_any,
        "totals": {k: cur[k] for k in ("income", "gross_expense", "refunds", "net_expense", "net_cash_flow", "savings_pct", "transaction_count")},
        "categories": [{"category": c, "amount": v, "pct": pct(v, cur["net_expense"]), "fixed": meta.get(c, {}).get("fixed", False)}
                       for c, v in cur["by_category"].items()],
        "income_categories": [{"category": c, "amount": v} for c, v in cur["income_by_category"].items()],
        "fixed_variable": fixed_variable_split(cur["by_category"], meta),
        "vs_previous_month": comparison(cur, prev),
        "vs_same_month_last_year": comparison(cur, yoy),
        "trend": trend,
    }


def yearly(db, uid, year: int) -> dict:
    s = datetime(year, 1, 1, tzinfo=timezone.utc)
    e = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    cur = period_totals(db, uid, s, e)
    prev = period_totals(db, uid, datetime(year - 1, 1, 1, tzinfo=timezone.utc), s)
    return {"year": year, "totals": {k: cur[k] for k in ("income", "net_expense", "net_cash_flow", "savings_pct")},
            "categories": [{"category": c, "amount": v} for c, v in cur["by_category"].items()],
            "vs_previous_year": comparison(cur, prev), "monthly": monthly_series(db, uid, s, e)}


def category_trends(db, uid, months: int = 6, today: date | None = None) -> dict:
    """Per-category monthly net spending for the latest `months` months with data, plus slope per month."""
    last = default_period(db, uid, today)
    first = add_months(last, -(months - 1))
    # Never count months before the user's first transaction as zero spending: "no data" is not "spent nothing".
    earliest = db.transactions.find_one({"user_id": uid, "type": {"$in": ["expense", "refund"]}}, sort=[("date", 1)], projection={"date": 1})
    if earliest:
        first = max(first, month_key(earliest["date"]))
    s, _ = month_bounds(first)
    _, e = month_bounds(last)
    series = monthly_category_series(db, uid, s, e)
    periods = [add_months(first, i) for i in range(months_between(first, last) + 1)]
    cats = sorted({c for m in series.values() for c in m})
    out = []
    for c in cats:
        vals = [series.get(p, {}).get(c, 0) for p in periods]
        n = len(vals)
        xs = list(range(n))
        mx, my = sum(xs) / n, sum(vals) / n
        denom = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / denom if denom else 0.0
        out.append({"category": c, "values": vals, "total": sum(vals), "avg_per_month": round(sum(vals) / n),
                    "trend_per_month": round(slope), "direction": "up" if slope > 0.02 * (my or 1) else "down" if slope < -0.02 * (my or 1) else "flat"})
    out.sort(key=lambda r: -r["total"])
    return {"periods": periods, "categories": out}


def health_score(db, uid, today: date | None = None) -> dict:
    """Transparent 0-100 score from up to four 25-point components. NOT a credit or banking score."""
    today = today or datetime.now(timezone.utc).date()
    hist = completed_month_history(db, uid, today, 6)
    comps = []
    user = db.users.find_one({"_id": uid}) or {}
    if hist:
        tot_inc = sum(h["income"] for h in hist)
        tot_flow = sum(h["net_cash_flow"] for h in hist)
        if tot_inc > 0:
            rate = tot_flow / tot_inc
            comps.append({"name": "Savings rate", "score": round(max(0.0, min(1.0, rate / 0.20)) * 25, 1), "max": 25,
                          "detail": f"Saved {rate * 100:.1f}% of income over {len(hist)} month(s); 20% or more earns full marks."})
        exp = [h["net_expense"] for h in hist]
        if len(exp) >= 3 and sum(exp) > 0:
            mean = sum(exp) / len(exp)
            var = sum((x - mean) ** 2 for x in exp) / len(exp)
            cv = (var ** 0.5) / mean if mean else 0
            comps.append({"name": "Spending stability", "score": round(max(0.0, 1 - cv / 0.5) * 25, 1), "max": 25,
                          "detail": f"Monthly spending varies by {cv * 100:.0f}% (coefficient of variation); no variation earns full marks, 50% or more earns none."})
        avg_exp = sum(exp) / len(exp)
        saved = int(user.get("current_savings") or 0)
        if avg_exp > 0 and user.get("current_savings") is not None:
            months_cov = saved / avg_exp
            comps.append({"name": "Emergency fund", "score": round(max(0.0, min(1.0, months_cov / 6)) * 25, 1), "max": 25,
                          "detail": f"Current savings cover {months_cov:.1f} month(s) of average spending; 6+ months earns full marks."})
    cur = month_key(today)
    b = db.budgets.find_one({"user_id": uid, "period": cur}) or db.budgets.find_one({"user_id": uid}, sort=[("period", -1)])
    if b:
        perf = budget_performance(db, uid, b["period"])
        if perf["total_budget"] > 0:
            over = sum(1 for r in perf["rows"] if r["budget"] > 0 and r["actual"] > r["budget"])
            n = sum(1 for r in perf["rows"] if r["budget"] > 0) or 1
            comps.append({"name": "Budget adherence", "score": round((1 - over / n) * 25, 1), "max": 25,
                          "detail": f"{n - over} of {n} budgeted categories within budget in {b['period']}."})
    if not comps:
        return {"score": None, "components": [], "note": "Not enough data yet. Import transactions and set a budget to see a score."}
    mx = sum(c["max"] for c in comps)
    score = round(sum(c["score"] for c in comps) / mx * 100)
    return {"score": score, "components": comps,
            "note": "A simple educational score computed from your own data using the formulas shown. It is not a credit score or financial advice."}


def budget_performance(db, uid, period: str) -> dict:
    b = db.budgets.find_one({"user_id": uid, "period": period})
    actual_tot = month_totals(db, uid, period)
    actual = actual_tot["by_category"]
    alloc = {a["category"]: a["amount"] for a in (b["allocations"] if b else [])}
    rows = []
    for c in sorted(set(alloc) | set(actual), key=lambda c: -(alloc.get(c, 0) or actual.get(c, 0))):
        bud, act = alloc.get(c, 0), actual.get(c, 0)
        rows.append({"category": c, "budget": bud, "actual": act, "remaining": bud - act,
                     "utilization_pct": pct(act, bud), "over_budget": bud > 0 and act > bud, "unbudgeted": c not in alloc and act > 0})
    total_budget = sum(alloc.values())
    return {"period": period, "exists": b is not None, "budget_id": str(b["_id"]) if b else None, "total_budget": total_budget,
            "total_actual": actual_tot["net_expense"], "remaining": total_budget - actual_tot["net_expense"],
            "utilization_pct": pct(actual_tot["net_expense"], total_budget), "rows": rows,
            "overspent_categories": [r["category"] for r in rows if r["over_budget"]]}


def recent_window(today: date, days: int) -> tuple[datetime, datetime]:
    end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=1)
    return end - timedelta(days=days), end
