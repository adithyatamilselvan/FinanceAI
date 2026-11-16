"""Lightweight statistical anomaly flags. Results are items to review, never fraud verdicts."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median, pstdev

from ..money import fmt, pct
from ..util import add_months, month_bounds, month_key
from . import analytics as an

DISCLAIMER = "These are statistical flags for you to review. A flagged item is unusual compared with your own history, not necessarily wrong or fraudulent."


def _mad(vals: list[int], med: float) -> float:
    return median([abs(v - med) for v in vals])


def detect(db, uid, currency: str = "INR") -> dict:
    last = db.transactions.find_one({"user_id": uid, "type": "expense"}, sort=[("date", -1)], projection={"date": 1})
    if not last:
        return {"large_transactions": [], "category_spikes": [], "possible_duplicates": [], "disclaimer": DISCLAIMER, "reference_date": None}
    ref = last["date"]
    since = ref - timedelta(days=365)
    txs = list(db.transactions.find({"user_id": uid, "type": "expense", "date": {"$gte": since}},
                                    projection={"date": 1, "amount": 1, "description": 1, "normalized_description": 1, "category": 1, "merchant_key": 1}).limit(20000))
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        by_cat[t["category"]].append(t)

    # A payee that recurs with a steady amount (fuel pump, rent, EMI) is normal for that payee even if it is large for the category.
    by_merchant: dict[str, list[int]] = defaultdict(list)
    for t in txs:
        if t.get("merchant_key"):
            by_merchant[t["merchant_key"]].append(t["amount"])

    def normal_for_payee(t: dict) -> bool:
        amts = by_merchant.get(t.get("merchant_key") or "", [])
        if len(amts) < 3:
            return False
        m = median(amts)
        return abs(t["amount"] - m) <= 0.25 * m

    large = []
    recent_cut = ref - timedelta(days=90)
    for cat, items in by_cat.items():
        if len(items) < 6:
            continue
        amts = [t["amount"] for t in items]
        med = median(amts)
        mad = _mad(amts, med)
        thresh = med + 3.5 * 1.4826 * mad if mad > 0 else 3 * med
        for t in items:
            if t["date"] >= recent_cut and t["amount"] > thresh and t["amount"] >= 2 * med and not normal_for_payee(t):
                ratio = t["amount"] / med if med else 0
                large.append({"transaction_id": str(t["_id"]), "date": t["date"].date().isoformat(), "description": t["description"],
                              "category": cat, "amount": t["amount"], "typical_amount": int(med), "ratio": round(ratio, 1),
                              "reason": f"{fmt(t['amount'], currency)} is {ratio:.1f}x the typical {cat} transaction ({fmt(int(med), currency)}, median of {len(items)})."})
    large.sort(key=lambda r: -r["ratio"])

    spikes = []
    ref_period = month_key(ref)
    s, _ = month_bounds(add_months(ref_period, -3))
    _, e = month_bounds(ref_period)
    series = an.monthly_category_series(db, uid, s, e)
    # Compare with up to 3 earlier months that actually contain data (need at least 2, so 3 months of history is enough).
    prior_periods = [p for p in (add_months(ref_period, -i) for i in (3, 2, 1)) if sum(series.get(p, {}).values()) > 0]
    cur = series.get(ref_period, {})
    if len(prior_periods) >= 2:
        k = len(prior_periods)
        avg_total = sum(sum(series[p].values()) for p in prior_periods) / k
        for cat, val in cur.items():
            prior = [series.get(p, {}).get(cat, 0) for p in prior_periods]
            mean = sum(prior) / k
            if mean <= 0:
                if val >= 0.05 * avg_total and val > 0:
                    spikes.append({"category": cat, "period": ref_period, "amount": val, "previous_average": 0, "increase": val, "increase_pct": None,
                                   "reason": f"New spending category in {ref_period}: {cat} {fmt(val, currency)} (no spending in the previous {k} month(s) of data)."})
                continue
            sd = max(pstdev(prior), 0.25 * mean)
            increase = val - mean
            if val >= 1.5 * mean and increase >= 0.02 * avg_total and increase > 2 * sd:
                spikes.append({"category": cat, "period": ref_period, "amount": val, "previous_average": int(mean),
                               "increase": int(increase), "increase_pct": pct(int(increase), int(mean)),
                               "reason": f"{cat} spending of {fmt(val, currency)} in {ref_period} is {pct(int(increase), int(mean))}% above the {fmt(int(mean), currency)} average of the previous {k} month(s)."})
        spikes.sort(key=lambda r: -r["increase"])

    dups = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    dup_cut = ref - timedelta(days=120)
    for t in txs:
        if t["date"] >= dup_cut:
            groups[(t["normalized_description"], t["amount"])].append(t)
    for (_, amt), items in groups.items():
        items.sort(key=lambda t: t["date"])
        for a, b in zip(items, items[1:]):
            if (b["date"] - a["date"]).days <= 2:
                dups.append({"transaction_ids": [str(a["_id"]), str(b["_id"])], "dates": [a["date"].date().isoformat(), b["date"].date().isoformat()],
                             "description": a["description"], "amount": amt,
                             "reason": f"Two payments of {fmt(amt, currency)} to the same payee within {(b['date'] - a['date']).days} day(s)."})
    dups.sort(key=lambda r: r["dates"][1], reverse=True)
    del txs
    return {"large_transactions": large[:50], "category_spikes": spikes[:20], "possible_duplicates": dups[:50],
            "disclaimer": DISCLAIMER, "reference_date": ref.date().isoformat(), "generated_at": datetime.now(timezone.utc).isoformat()}
