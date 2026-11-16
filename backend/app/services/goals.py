"""Savings-goal and emergency-fund calculations (integer minor units, explicit assumptions)."""
import calendar
from datetime import date

from ..money import ceil_div, pct

DAYS_PER_MONTH = 30.4375


def add_months_to_date(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_until(today: date, target: date) -> int:
    """Whole months remaining (rounded up, minimum 1 if the target is in the future, 0 if passed)."""
    days = (target - today).days
    if days <= 0:
        return 0
    return max(1, ceil_div(days * 1000, int(DAYS_PER_MONTH * 1000)))


def goal_view(g: dict, today: date) -> dict:
    target, cur = g["target_amount"], g["current_amount"]
    remaining = max(0, target - cur)
    tdate = g.get("target_date")
    tdate = tdate.date() if hasattr(tdate, "date") and tdate is not None else tdate
    planned = g.get("monthly_contribution") or 0
    months_left = months_until(today, tdate) if tdate else None
    required = None
    if remaining == 0:
        required = 0
    elif months_left:
        required = ceil_div(remaining, months_left)
    est = None
    if remaining == 0:
        est = today
    elif planned > 0:
        est = add_months_to_date(today, ceil_div(remaining, planned))
    on_track = None
    if tdate and est:
        on_track = est <= tdate
    assumptions = [
        "Months are counted as 30.4375 days and rounded up.",
        "The estimated completion date assumes the planned monthly contribution is paid every month with no investment return.",
    ]
    if not planned and remaining:
        assumptions.append("Set a planned monthly contribution to get an estimated completion date.")
    return {
        "id": str(g["_id"]), "name": g["name"], "target_amount": target, "current_amount": cur, "remaining": remaining,
        "progress_pct": pct(min(cur, target), target), "target_date": tdate.isoformat() if tdate else None,
        "monthly_contribution": planned or None, "months_remaining": months_left, "required_monthly_contribution": required,
        "estimated_completion": est.isoformat() if est else None, "on_track": on_track, "achieved": remaining == 0,
        "assumptions": assumptions, "created_at": g["created_at"].isoformat(),
    }


def emergency_fund(avg_monthly_expense: int, months: int, current_savings: int, monthly_contribution: int, basis: str, months_of_history: int) -> dict:
    target = avg_monthly_expense * months
    gap = max(0, target - current_savings)
    to_goal = ceil_div(gap, monthly_contribution) if gap and monthly_contribution > 0 else (0 if gap == 0 else None)
    return {
        "basis": basis, "months_of_cover": months, "average_monthly_expense": avg_monthly_expense, "target": target,
        "current_savings": current_savings, "gap": gap, "coverage_months": round(current_savings / avg_monthly_expense, 1) if avg_monthly_expense else None,
        "monthly_contribution": monthly_contribution or None, "months_to_goal": to_goal, "history_months_used": months_of_history,
        "assumptions": [
            f"Average monthly {'essential ' if basis == 'essential' else ''}spending is taken from {months_of_history} completed month(s) of your transactions.",
            "Essential = Housing, Groceries, Healthcare, Utilities, Insurance, Loan Repayment, Education and your own essential categories." if basis == "essential" else "All net spending is treated as the amount to cover.",
            "Months to goal assumes a constant contribution with no interest earned.",
        ],
    }
