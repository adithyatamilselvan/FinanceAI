"""Explainable forecasting: weighted moving average + damped linear trend, with an empirical range.

Refuses to forecast with fewer than MIN_MONTHS of completed-month history.
"""
from datetime import date, datetime, timezone

import numpy as np

from ..money import ceil_div
from ..util import add_months, month_key, months_between
from . import analytics as an

MIN_MONTHS = 3


def _project(values: list[int], horizon: int) -> dict:
    v = np.array(values, dtype=float)
    n = len(v)
    k = min(3, n)
    w = np.arange(1, k + 1, dtype=float)
    baseline = float(np.dot(v[-k:], w) / w.sum())
    window = v[-12:]
    slope = 0.0
    if len(window) >= 4:
        slope = float(np.polyfit(np.arange(len(window)), window, 1)[0])
    sd = float(np.std(window, ddof=1)) if len(window) >= 2 else 0.0
    out = []
    for h in range(1, horizon + 1):
        adj = slope * h * 0.5  # damped: only half the fitted slope is extrapolated
        cap = 0.2 * abs(baseline)
        adj = max(-cap, min(cap, adj))
        mid = max(0.0, baseline + adj)
        out.append({"mid": int(round(mid)), "low": int(round(max(0.0, mid - sd))), "high": int(round(mid + sd))})
    return {"points": out, "baseline": int(round(baseline)), "slope_per_month": int(round(slope)), "stdev": int(round(sd))}


def forecast(db, uid, horizon: int = 3, today: date | None = None, goals: list[dict] | None = None, recurring: list[dict] | None = None) -> dict:
    hist = an.completed_month_history(db, uid, today, 24)
    n = len(hist)
    base = {"history_months": n, "required_months": MIN_MONTHS}
    if n < MIN_MONTHS:
        return {**base, "sufficient": False,
                "reason": f"Forecasting needs at least {MIN_MONTHS} completed months of transactions; you have {n}. Import more history to enable it."}
    periods = [h["period"] for h in hist]
    gaps = []
    for a, b in zip(periods, periods[1:]):
        if add_months(a, 1) != b:
            gaps.append(f"{a} to {b}")
    exp = _project([h["net_expense"] for h in hist], horizon)
    inc = _project([h["income"] for h in hist], horizon)
    first_future = add_months(periods[-1], 1)
    stale = max(0, months_between(periods[-1], add_months(month_key(today or datetime.now(timezone.utc).date()), -1)))
    user = db.users.find_one({"_id": uid}) or {}
    start_savings = int(user.get("current_savings") or 0)
    sd_net = (inc["stdev"] ** 2 + exp["stdev"] ** 2) ** 0.5
    months = []
    cum = start_savings
    for i in range(horizon):
        p = add_months(first_future, i)
        net = inc["points"][i]["mid"] - exp["points"][i]["mid"]
        cum += net
        spread = int(round(sd_net * (i + 1) ** 0.5))
        months.append({"period": p, "income": inc["points"][i], "expense": exp["points"][i], "net_savings": net,
                       "cumulative_savings": cum, "cumulative_low": cum - spread, "cumulative_high": cum + spread})
    rec_proj = None
    if recurring is not None:
        from .recurring import project_months

        rec_proj = project_months(recurring, first_future, horizon)
        for m in months:
            m["recurring_expected"] = rec_proj.get(m["period"], 0)
    avg_net = int(sum(m["net_savings"] for m in months) / horizon)
    goal_est = []
    for g in goals or []:
        remaining = max(0, g["target_amount"] - g["current_amount"])
        if remaining == 0:
            goal_est.append({"goal_id": g["id"], "name": g["name"], "status": "achieved", "months_needed": 0})
        elif avg_net > 0:
            goal_est.append({"goal_id": g["id"], "name": g["name"], "status": "projected", "months_needed": ceil_div(remaining, avg_net)})
        else:
            goal_est.append({"goal_id": g["id"], "name": g["name"], "status": "not_reachable_at_current_trend", "months_needed": None})
    confidence = "low" if n < 6 else "medium" if n < 12 else "higher"
    return {
        **base, "sufficient": True, "history_from": periods[0], "history_to": periods[-1], "gaps": gaps, "confidence": confidence,
        "stale_months": stale, "months": months, "avg_projected_monthly_savings": avg_net, "starting_savings": start_savings,
        "expense_trend_per_month": exp["slope_per_month"], "income_trend_per_month": inc["slope_per_month"],
        "goal_estimates": goal_est,
        "method": [
            "Baseline = weighted moving average of the last 3 completed months (weights 1,2,3).",
            "Trend = half of the straight-line slope over the last up to 12 months (only when 4+ months), capped at +/-20% of the baseline.",
            "Range = one standard deviation of historical monthly values (a typical range, not a guarantee). Cumulative range widens with the square root of months assuming income and expenses vary independently.",
            "Goal estimates assume 100% of projected savings go to that single goal.",
            f"Based on {n} completed month(s) ({periods[0]} to {periods[-1]}); the current month is excluded because it is incomplete. {('Missing months: ' + ', '.join(gaps) + '.') if gaps else ''}".strip(),
            "Forecasts are statistical extrapolations of your past behaviour. Real results will differ, especially with short history.",
        ],
    }
