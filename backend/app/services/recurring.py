"""Recurring-payment detection from transaction history, with the evidence behind every detection."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median

from ..money import mul_div_round
from ..util import now_utc

FREQS = {"weekly": (7, 1), "monthly": (30, 4), "quarterly": (91, 7), "annual": (365, 12)}
MONTHLY_FACTOR = {"weekly": (52, 12), "monthly": (1, 1), "quarterly": (1, 3), "annual": (1, 12)}
MIN_CONFIDENCE = 0.5
# Categories whose recurring payments are obligations/subscriptions. Everything else (groceries, fuel, cabs...) is a
# habit: it is listed for review but not counted in the "recurring cost" total unless the user confirms it.
BILL_CATEGORIES = {"Housing", "Utilities", "Insurance", "Loan Repayment", "Subscriptions", "Education"}


def kind_of(category: str | None) -> str:
    return "bill_or_subscription" if category in BILL_CATEGORIES else "habitual_spending"


def estimated_monthly(amount: int, frequency: str) -> int:
    n, d = MONTHLY_FACTOR[frequency]
    return mul_div_round(amount, n, d)


def _classify(intervals: list[int]) -> tuple[str, float] | None:
    med = median(intervals)
    best = min(FREQS, key=lambda f: abs(FREQS[f][0] - med))
    nominal, tol = FREQS[best]
    if abs(nominal - med) > tol:
        return None
    consistency = sum(1 for i in intervals if abs(i - nominal) <= tol) / len(intervals)
    return best, consistency


def detect_candidates(txs: list[dict], ref_date: date) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        if t.get("merchant_key"):
            groups[t["merchant_key"]].append(t)
    out = []
    for key, items in groups.items():
        items.sort(key=lambda t: t["date"])
        dates = sorted({t["date"].date() for t in items})
        if len(dates) < 2:
            continue
        intervals = [(b - a).days for a, b in zip(dates, dates[1:])]
        cls = _classify(intervals)
        if cls is None:
            continue
        freq, ic = cls
        n = len(dates)
        if freq != "annual" and n < 3:
            continue
        amounts = [t["amount"] for t in items]
        med_amt = int(median(amounts))
        ac = sum(1 for a in amounts if abs(a - med_amt) <= 0.10 * med_amt) / len(amounts)
        # variable bills (electricity, water) legitimately vary: accept up to +/-40% around the median, with less confidence
        ac_loose = sum(1 for a in amounts if abs(a - med_amt) <= 0.40 * med_amt) / len(amounts)
        if ic < 0.6 or ac_loose < 0.8:
            continue
        nominal = FREQS[freq][0]
        recency = 1.0 if (ref_date - dates[-1]).days <= 1.5 * nominal else 0.0
        need = 3 if freq == "annual" else 6
        conf = round(0.4 * ic + 0.15 * ac + 0.15 * ac_loose + 0.2 * min(1.0, n / need) + 0.1 * recency, 2)
        if conf < MIN_CONFIDENCE:
            continue
        name = Counter(t["description"] for t in items).most_common(1)[0][0]
        cat = Counter(t["category"] for t in items).most_common(1)[0][0]
        out.append({
            "merchant_key": key, "name": name, "category": cat, "amount": med_amt, "frequency": freq, "confidence": conf,
            "estimated_monthly": estimated_monthly(med_amt, freq),
            "next_expected": (dates[-1] + timedelta(days=nominal)).isoformat(),
            "last_seen": dates[-1].isoformat(),
            "evidence": {
                "occurrences": n, "dates": [d.isoformat() for d in dates[-6:]], "median_interval_days": float(median(intervals)),
                "interval_consistency": round(ic, 2), "amount_consistency": round(ac, 2), "amount_consistency_loose": round(ac_loose, 2), "min_amount": min(amounts), "max_amount": max(amounts),
                "active": bool(recency),
                "explanation": f"{n} payments about every {int(median(intervals))} days; {round(ic * 100)}% of gaps are regular and {round(ac * 100)}% of amounts are within 10% of the median ({round(ac_loose * 100)}% within 40%).",
            },
        })
    out.sort(key=lambda c: -c["estimated_monthly"])
    return out


def detect_and_store(db, uid, today: date | None = None) -> dict:
    last = db.transactions.find_one({"user_id": uid, "type": "expense"}, sort=[("date", -1)], projection={"date": 1})
    if not last:
        return {"detected": 0, "items": []}
    ref = last["date"].date()
    since = datetime(ref.year, ref.month, ref.day, tzinfo=timezone.utc) - timedelta(days=550)
    txs = list(db.transactions.find({"user_id": uid, "type": "expense", "date": {"$gte": since}},
                                    projection={"date": 1, "amount": 1, "description": 1, "merchant_key": 1, "category": 1}).limit(20000))
    cands = detect_candidates(txs, ref)
    del txs
    now = now_utc()
    for c in cands:
        existing = db.recurring_payments.find_one({"user_id": uid, "merchant_key": c["merchant_key"]})
        if existing:
            upd = {"evidence": c["evidence"], "confidence": c["confidence"], "next_expected": c["next_expected"],
                   "last_seen": c["last_seen"], "category": c["category"], "updated_at": now}
            if not existing.get("user_edited"):
                upd.update(amount=c["amount"], frequency=c["frequency"], name=c["name"], estimated_monthly=c["estimated_monthly"])
            db.recurring_payments.update_one({"_id": existing["_id"]}, {"$set": upd})
        else:
            db.recurring_payments.insert_one({**c, "user_id": uid, "status": "detected", "user_edited": False, "created_at": now, "updated_at": now})
    return {"detected": len(cands)}


def recurring_out(d: dict) -> dict:
    return {"id": str(d["_id"]), "name": d["name"], "merchant_key": d["merchant_key"], "category": d.get("category"), "amount": d["amount"],
            "frequency": d["frequency"], "status": d["status"], "confidence": d["confidence"],
            "estimated_monthly": d["estimated_monthly"], "kind": kind_of(d.get("category")), "next_expected": d.get("next_expected"), "last_seen": d.get("last_seen"),
            "evidence": d.get("evidence"), "user_edited": d.get("user_edited", False)}


def project_months(items: list[dict], start_period: str, months: int) -> dict[str, int]:
    """Expected recurring spend per future period from active (not dismissed) recurring payments."""
    from ..util import add_months, month_key

    out = {add_months(start_period, i): 0 for i in range(months)}
    end_period = add_months(start_period, months - 1)
    for it in items:
        if it["status"] == "dismissed" or not it.get("next_expected"):
            continue
        d = date.fromisoformat(it["next_expected"])
        step = FREQS[it["frequency"]][0]
        guard = 0
        while month_key(d) <= end_period and guard < 500:
            k = month_key(d)
            if k in out:
                out[k] += it["amount"]
            d += timedelta(days=step)
            guard += 1
    return out
