"""Budget optimisation: constrained least-"pain" reduction of variable spending.

Problem (per scenario), for each reducible category i with current monthly spend c_i:
    minimise    sum_i  w_i * c_i * r_i^2          (pain grows quadratically with the fraction r_i cut)
    subject to  sum_i  c_i * r_i  >=  T            (savings target)
                0 <= r_i <= rmax_i                 (per-category cap from essentiality and user minimum)
Solved with SciPy SLSQP; the result is rounded to integer minor units, re-validated against every constraint,
and replaced by a deterministic water-filling solution if validation fails. Fixed categories are never changed.
Floats are used only inside the solver; outputs and validation are integers.
"""
from . import analytics as an

SCENARIOS = {
    # name: (target as share of reducible spend in basis points, max cut by class in basis points)
    "conservative": {"target_bp": 500, "cut_bp": {"discretionary": 1500, "semi": 500, "essential": 0}},
    "balanced": {"target_bp": 1000, "cut_bp": {"discretionary": 3000, "semi": 1000, "essential": 500}},
    "aggressive": {"target_bp": 1800, "cut_bp": {"discretionary": 5000, "semi": 2000, "essential": 1000}},
}
PAIN = {"discretionary": 1.0, "semi": 3.0, "essential": 8.0}
SEMI_ESSENTIAL = {"Transport"}


def _klass(cat: str, meta: dict) -> str:
    if cat in SEMI_ESSENTIAL:
        return "semi"
    if meta.get(cat, {}).get("essential"):
        return "essential"
    return "discretionary"


def _waterfill(c: list[int], w: list[float], cap: list[int], target: int) -> list[int]:
    """Deterministic fallback: r_i = min(cap_i/c_i, lam / w_i); bisection on lam. Returns reductions in minor units."""
    if sum(cap) <= target:
        return list(cap)
    lo, hi = 0.0, 1e6
    for _ in range(200):
        lam = (lo + hi) / 2
        total = sum(min(cap[i], lam / w[i] * c[i]) for i in range(len(c)))
        lo, hi = (lam, hi) if total < target else (lo, lam)
    red = [min(cap[i], int(hi / w[i] * c[i])) for i in range(len(c))]
    return _topup(red, w, cap, target)


def _topup(red: list[int], w: list[float], cap: list[int], target: int) -> list[int]:
    short = target - sum(red)
    for i in sorted(range(len(red)), key=lambda j: w[j]):
        if short <= 0:
            break
        add = min(short, cap[i] - red[i])
        red[i] += add
        short -= add
    return red


def _solve(c: list[int], w: list[float], cap: list[int], target: int) -> tuple[list[int], str]:
    n = len(c)
    if n == 0 or target <= 0:
        return [0] * n, "none"
    if sum(cap) <= target:
        return list(cap), "cap-limited"
    try:
        import numpy as np
        from scipy.optimize import minimize

        scale = max(c) or 1
        cs = np.array(c, dtype=float) / scale
        ws = np.array(w, dtype=float)
        caps = np.array(cap, dtype=float) / scale
        t = target / scale
        res = minimize(
            lambda x: float(np.sum(ws * x * x / np.maximum(cs, 1e-12))),  # r_i = x_i / c_i  =>  w c r^2 = w x^2 / c
            x0=np.minimum(caps, t / n), method="SLSQP", bounds=[(0, float(cap_i)) for cap_i in caps],
            constraints=[{"type": "ineq", "fun": lambda x: float(np.sum(x) - t)}], options={"maxiter": 200, "ftol": 1e-12},
        )
        red = [int(max(0, min(cap[i], round(res.x[i] * scale)))) for i in range(n)]
        red = _topup(red, w, cap, target)
        if res.success and sum(red) >= target and all(0 <= red[i] <= cap[i] for i in range(n)):
            return red, "scipy-slsqp"
    except Exception:  # solver problems must never break the endpoint; fall back below
        pass
    return _waterfill(c, w, cap, target), "water-filling-fallback"


def validate(current: dict[str, int], suggested: dict[str, int], floors: dict[str, int], target: int, achieved: int, feasible_max: int) -> list[str]:
    errs = []
    for cat, cur in current.items():
        s = suggested[cat]
        if s > cur:
            errs.append(f"{cat}: suggestion exceeds current spending")
        if s < floors[cat]:
            errs.append(f"{cat}: suggestion below allowed minimum")
        if s < 0:
            errs.append(f"{cat}: negative suggestion")
    if achieved < min(target, feasible_max):
        errs.append("savings target not met although feasible")
    return errs


def optimize(db, uid, months: int, min_spend: dict[str, int], today=None) -> dict:
    hist = an.completed_month_history(db, uid, today, months)
    if not hist:
        return {"sufficient": False, "reason": "Optimisation needs at least one completed month of transactions."}
    from ..util import month_bounds

    periods = [h["period"] for h in hist][-months:]
    s, _ = month_bounds(periods[0])
    _, e = month_bounds(periods[-1])
    series = an.monthly_category_series(db, uid, s, e)
    n = len(periods)
    totals: dict[str, int] = {}
    for p in periods:
        for cat, v in series.get(p, {}).items():
            totals[cat] = totals.get(cat, 0) + v
    avg = {c: v // n for c, v in totals.items() if v // n > 0}
    meta = an.category_meta(db, uid)
    fixed = {c: v for c, v in avg.items() if meta.get(c, {}).get("fixed")}
    variable = {c: v for c, v in avg.items() if c not in fixed}
    if not variable:
        return {"sufficient": False, "reason": "No variable spending found to optimise."}
    cats = sorted(variable, key=lambda c: -variable[c])
    c_list = [variable[c] for c in cats]
    total_variable = sum(c_list)
    user_min = {k: v for k, v in min_spend.items() if k in variable}
    scenarios = {}
    for name, cfg in SCENARIOS.items():
        caps, floors, w = [], {}, []
        for cat in cats:
            kl = _klass(cat, meta)
            floor = max(variable[cat] - variable[cat] * cfg["cut_bp"][kl] // 10000, min(user_min.get(cat, 0), variable[cat]))
            floors[cat] = floor
            caps.append(variable[cat] - floor)
            w.append(PAIN[kl])
        target = total_variable * cfg["target_bp"] // 10000
        feasible = sum(caps)
        red, solver = _solve(c_list, w, caps, target)
        suggested = {cat: variable[cat] - red[i] for i, cat in enumerate(cats)}
        achieved = sum(red)
        errs = validate(variable, suggested, floors, target, achieved, feasible)
        if errs:  # solver output failed validation: use the deterministic fallback
            red = _waterfill(c_list, w, caps, target)
            suggested = {cat: variable[cat] - red[i] for i, cat in enumerate(cats)}
            achieved, solver = sum(red), "water-filling-fallback"
            errs = validate(variable, suggested, floors, target, achieved, feasible)
        scenarios[name] = {
            "name": name, "target_savings": target, "achieved_savings": achieved, "annual_savings": achieved * 12,
            "target_reached": achieved >= target, "solver": solver, "valid": not errs, "validation_errors": errs,
            "categories": [{"category": cat, "current": variable[cat], "suggested": suggested[cat], "change": suggested[cat] - variable[cat],
                            "change_pct": round((suggested[cat] - variable[cat]) * 100 / variable[cat], 1), "minimum_allowed": floors[cat],
                            "class": _klass(cat, meta)} for cat in cats],
        }
    return {
        "sufficient": True, "basis_periods": periods, "history_months": n, "current_variable_total": total_variable,
        "fixed_categories": [{"category": c, "monthly": v} for c, v in sorted(fixed.items(), key=lambda kv: -kv[1])],
        "scenarios": scenarios, "warnings": ([f"Only {n} month(s) of history used; averages may not be representative."] if n < 3 else []),
        "disclaimer": "These are estimates based on your past spending, not guaranteed savings. Nothing has been changed in your budget. Fixed commitments are never reduced.",
        "method": "Minimises a weighted squared-cut penalty subject to hitting the scenario savings target, per-category maximum cuts (by essential/semi-essential/discretionary class) and your minimum-spend limits (SciPy SLSQP, validated, with a deterministic fallback).",
    }


def budget_allocations(rec: dict, scenario: str) -> list[dict]:
    sc = rec["scenarios"][scenario]
    allocs = [{"category": c["category"], "amount": c["suggested"]} for c in sc["categories"]]
    allocs += [{"category": f["category"], "amount": f["monthly"]} for f in rec["fixed_categories"]]
    return allocs

