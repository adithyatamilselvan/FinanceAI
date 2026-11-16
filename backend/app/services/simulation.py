"""What-if simulation: loan EMI/amortisation and savings accumulation.

All money values are integer minor units; interest uses Decimal with round-half-up at each month.
Investment growth is an illustrative constant monthly rate, not a prediction or advice.
"""
from decimal import ROUND_HALF_UP, Decimal, getcontext

from ..money import mul_div_round

getcontext().prec = 40


def _r(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


LOAN_TOO_SMALL = ("This loan cannot be repaid in whole paise: the monthly instalment rounds to zero or does not cover the monthly interest. "
                  "Increase the loan amount, or reduce the interest rate or tenure.")
MAX_LOAN_TENURE = 600


def validate_loan(principal: int, annual_rate_pct: float, tenure: int, extra: int, max_amount: int = 10**13) -> None:
    """Raise ValueError with a user-safe message for anything the amortisation cannot represent."""
    if not isinstance(principal, int) or isinstance(principal, bool) or not 0 < principal <= max_amount:
        raise ValueError("Loan amount must be greater than zero.")
    if annual_rate_pct is None or annual_rate_pct != annual_rate_pct or not 0 <= annual_rate_pct <= 100:
        raise ValueError("Annual interest rate must be between 0 and 100 (enter 0 for an interest-free loan).")
    if not isinstance(tenure, int) or isinstance(tenure, bool) or not 1 <= tenure <= MAX_LOAN_TENURE:
        raise ValueError(f"Tenure must be a whole number of months between 1 and {MAX_LOAN_TENURE}.")
    if not isinstance(extra, int) or isinstance(extra, bool) or not 0 <= extra <= max_amount:
        raise ValueError("Extra monthly payment must be zero or more.")


def emi(principal: int, annual_rate_pct: float, n: int) -> int:
    """Standard EMI = P*r*(1+r)^n / ((1+r)^n - 1), r = annual/12/100. Rounded half-up to the minor unit."""
    if n <= 0:
        raise ValueError("tenure must be positive")
    r = Decimal(str(annual_rate_pct)) / Decimal(1200)
    if r == 0:
        out = _r(Decimal(principal) / n)
    else:
        f = (1 + r) ** n
        out = _r(Decimal(principal) * r * f / (f - 1))
    if principal > 0 and out <= 0:
        raise ValueError(LOAN_TOO_SMALL)
    return out


def amortize(principal: int, annual_rate_pct: float, n: int, extra: int = 0) -> dict:
    """Month-by-month amortisation. The final payment is adjusted so the balance ends exactly at zero."""
    r = Decimal(str(annual_rate_pct)) / Decimal(1200)
    pay = emi(principal, annual_rate_pct, n)
    bal, total_interest, months = principal, 0, 0
    sched = []
    while bal > 0 and months < 1200:
        months += 1
        interest = _r(Decimal(bal) * r)
        due = min(bal + interest, pay + extra)
        if months == n and extra == 0:
            due = bal + interest  # absorb rounding drift on the scheduled last instalment
        princ = due - interest
        if princ <= 0:
            raise ValueError(LOAN_TOO_SMALL)
        bal -= princ
        total_interest += interest
        sched.append({"month": months, "payment": due, "interest": interest, "principal": princ, "balance": bal})
    return {"emi": pay, "months": months, "total_interest": total_interest, "total_paid": principal + total_interest, "schedule": sched}


def loan_analysis(principal: int, annual_rate_pct: float, tenure: int, extra: int) -> dict:
    validate_loan(principal, annual_rate_pct, tenure, extra)
    base = amortize(principal, annual_rate_pct, tenure, 0)
    out = {
        "emi": base["emi"], "months": base["months"], "total_interest": base["total_interest"], "total_paid": base["total_paid"],
        "first_12_months": base["schedule"][:12],
        "assumptions": ["Fixed rate, monthly compounding, payment at month end.", "EMI = P*r*(1+r)^n / ((1+r)^n - 1) with r = annual rate / 12.",
                        "Amounts round half-up to the minor unit each month; the last payment absorbs rounding."],
    }
    if extra > 0:
        fast = amortize(principal, annual_rate_pct, tenure, extra)
        out["with_extra_payment"] = {"extra_monthly_payment": extra, "months": fast["months"], "months_saved": base["months"] - fast["months"],
                                     "total_interest": fast["total_interest"], "interest_saved": base["total_interest"] - fast["total_interest"]}
    return out


def accumulate(start: int, monthly: list[int], annual_return_pct: float) -> list[int]:
    """End-of-month balances (r = annual/12, illustrative).

    Positive opening balance: balance = round(balance * (1 + r)) + contribution  (the original calculation, unchanged to the paisa).
    Zero or negative opening balance: balance = balance + contribution. A shortfall is not an invested asset, so it neither earns
    nor is multiplied by a growth factor. When the balance turns positive, growth applies to it from the following month.
    Baseline and scenario both use this one function.
    """
    r = Decimal(str(annual_return_pct)) / Decimal(1200)
    bal, out = start, []
    for m in monthly:
        bal = (_r(Decimal(bal) * (1 + r)) if bal > 0 else bal) + m
        out.append(bal)
    return out


def first_reaching(series: list[int], target: int) -> int | None:
    for i, v in enumerate(series, start=1):
        if v >= target:
            return i
    return None


def simulate(p: dict, base_income: int, base_expense: int, cat_avg: dict[str, int], start_savings: int, scenario_start_savings: int | None = None) -> dict:
    """`start_savings` is the BASELINE starting balance (the user's real savings). `scenario_start_savings` is what the scenario assumes
    instead (defaults to the baseline value). Their difference is reported separately from monthly changes."""
    h = p["horizon_months"]
    scen_start = start_savings if scenario_start_savings is None else scenario_start_savings
    income1 = base_income + p["income_increase_amount"] + mul_div_round(base_income, int(round(p["income_increase_pct"] * 100)), 10000)
    cut_total = 0
    cuts_applied = []
    for cut in p["category_cuts"]:
        cur = cat_avg.get(cut["category"], 0)
        amt = mul_div_round(cur, int(round(cut["percent"] * 100)), 10000)
        cut_total += amt
        cuts_applied.append({"category": cut["category"], "current_monthly": cur, "percent": cut["percent"], "monthly_saving": amt})
    expense1 = max(0, base_expense - cut_total)
    base_monthly = base_income - base_expense
    scen_monthly = income1 - expense1 + p["extra_monthly_saving"]

    loan = None
    base_loan_adj = [0] * h   # monthly cash-flow effect of the loan on the BASELINE series
    scen_loan_adj = [0] * h   # ... and on the SCENARIO series
    if p.get("loan"):
        ln = p["loan"]
        mode = ln.get("mode", "existing")
        loan = loan_analysis(ln["principal"], ln["annual_rate_pct"], ln["tenure_months"], ln["extra_monthly_payment"])
        # The two ACTUAL repayment schedules (baseline: regular EMI only; scenario: EMI plus the extra payment). Payments are 0 after payoff,
        # and the last payment is capped at the outstanding balance plus interest by amortize().
        pay_base = [r["payment"] for r in amortize(ln["principal"], ln["annual_rate_pct"], ln["tenure_months"], 0)["schedule"]]
        pay_scen = [r["payment"] for r in amortize(ln["principal"], ln["annual_rate_pct"], ln["tenure_months"], ln["extra_monthly_payment"])["schedule"]]
        pb = [pay_base[i] if i < len(pay_base) else 0 for i in range(h)]
        ps = [pay_scen[i] if i < len(pay_scen) else 0 for i in range(h)]
        regular = loan["emi"]
        # existing loan: the regular EMI is already inside base spending, so only the DIFFERENCE from it moves savings (never deducted twice;
        #                after payoff the freed EMI is added back). new loan: every scheduled payment is deducted from savings.
        embedded = regular if mode == "existing" else 0
        base_loan_adj = [embedded - pb[i] for i in range(h)]
        scen_loan_adj = [embedded - ps[i] for i in range(h)]
        loan["mode"] = mode
        loan["cash_flow"] = {
            "embedded_emi": embedded,
            "baseline_payments": pb,
            "scenario_regular": [min(x, regular) for x in ps],            # regular instalment part of each scenario payment
            "scenario_extra": [max(0, x - regular) for x in ps],          # extra part (zero after payoff and in the capped last payment)
            "net_vs_baseline": sum(scen_loan_adj) - sum(base_loan_adj),   # + = cash freed vs baseline, - = extra cash paid to the loan
        }
        loan["assumptions"] = loan["assumptions"] + [
            ("Existing loan: your regular spending already includes this EMI, so projected savings move only by the difference between the scenario's repayments and that EMI "
             "(extra payments reduce savings; once the loan is paid off the freed EMI is added back). The EMI is never deducted twice.")
            if mode == "existing" else
            ("New loan: the EMI is not part of your regular spending, so every scheduled repayment is deducted from projected savings in both the baseline and the scenario. "
             "The comparison therefore shows the effect of extra payments."),
            "Repayments stop at payoff; the last payment is capped at the remaining balance plus interest.",
        ]
    base_flows = [base_monthly + base_loan_adj[i] for i in range(h)]
    scen_flows = [scen_monthly + scen_loan_adj[i] for i in range(h)]
    base_series = accumulate(start_savings, base_flows, p["annual_return_pct"])
    scen_series = accumulate(scen_start, scen_flows, p["annual_return_pct"])
    # Split the final-balance difference into its sources so starting savings is never mislabelled as monthly saving.
    from_start_savings = scen_start - start_savings                      # one-off difference in the opening balance
    from_monthly_changes = (scen_monthly - base_monthly) * h              # extra monthly saving, income change, category cuts (NOT loan flows)
    from_loan_cash_flow = sum(scen_loan_adj) - sum(base_loan_adj)         # net loan cash-flow difference: extra payments (-) and freed EMI (+)
    final_diff = scen_series[-1] - base_series[-1]
    from_growth = final_diff - from_start_savings - from_monthly_changes - from_loan_cash_flow  # illustrative return (0 when none assumed)

    res = {
        "horizon_months": h, "annual_return_pct": p["annual_return_pct"],
        "negative_balance_months": {"baseline": sum(1 for b in base_series if b < 0), "scenario": sum(1 for b in scen_series if b < 0)},
        "starting_savings": scen_start, "baseline_starting_savings": start_savings, "scenario_starting_savings": scen_start,
        "baseline": {"monthly_income": base_income, "monthly_expense": base_expense, "monthly_savings": base_monthly, "final_balance": base_series[-1]},
        "scenario": {"monthly_income": income1, "monthly_expense": expense1, "monthly_savings": scen_monthly, "final_balance": scen_series[-1],
                     "category_cuts": cuts_applied},
        "difference": {"monthly_savings": scen_monthly - base_monthly, "final_balance": final_diff,
                       "total_extra_saved": from_monthly_changes + from_loan_cash_flow,  # all cash-flow differences, before growth (kept for compatibility)
                       "starting_savings": from_start_savings, "from_monthly_changes": from_monthly_changes,
                       "from_loan_cash_flow": from_loan_cash_flow, "from_growth": from_growth},
        "series": {"baseline": base_series, "scenario": scen_series},
        "loan": loan,
        "assumptions": [
            "Baseline = your average monthly income and net spending from real completed months (or the values you entered), starting from the savings balance in your profile.",
            "Starting savings entered for the scenario replaces the opening balance of the scenario only; the baseline keeps your profile balance. The difference is shown separately from monthly changes.",
            "'Extra monthly saving' is treated as money moved from spending to savings (it is added to the scenario's monthly savings).",
            "Income and spending stay constant every month; no inflation or tax effects are modelled.",
            f"Investment growth is a constant illustrative {p['annual_return_pct']}% per year compounded monthly, earned only on a positive savings balance: "
            "a zero or negative balance earns no return and is never grown. It is not a prediction, guarantee or personalised investment advice."
            if p["annual_return_pct"] else "No investment return is assumed.",
        ],
    }
    if p.get("goal_target"):
        res["goal"] = {"target": p["goal_target"], "baseline_month": first_reaching(base_series, p["goal_target"]),
                       "scenario_month": first_reaching(scen_series, p["goal_target"]),
                       "note": "null means the target is not reached within the horizon."}
    if p.get("emergency_fund_months"):
        tb, ts = base_expense * p["emergency_fund_months"], expense1 * p["emergency_fund_months"]
        res["emergency_fund"] = {"months_of_cover": p["emergency_fund_months"], "baseline_target": tb, "scenario_target": ts,
                                 "baseline_month": first_reaching(base_series, tb), "scenario_month": first_reaching(scen_series, ts)}
    return res
