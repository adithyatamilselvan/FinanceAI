"""Loan / EMI inside the what-if simulation.

Expected values are independent of the application code: either worked out by hand or produced by `ref_schedule` below, an exact-Fraction
amortisation (interest rounded half-up each month, final payment capped at balance + interest).

Baseline spending used throughout: income 10,000,000 paise, expense 9,000,000, i.e. savings of 1,000,000 paise (Rs 10,000) a month.
"""
from fractions import Fraction

import pytest

from app.services import simulation as sim
from tests.conftest import API

INC, EXP, M = 10_000_000, 9_000_000, 1_000_000
TEN_LAKH = dict(principal=100_000_000, annual_rate_pct=10, tenure_months=120)
EMI_10L = 1_321_507


def rnd(x: Fraction) -> int:
    return int((x + Fraction(1, 2)).__floor__())


def ref_schedule(P, annual, n, extra=0):
    """Independent amortisation: returns (emi, [(month, payment, interest, principal)])."""
    r = Fraction(str(annual)) / 1200
    emi = rnd(Fraction(P, n)) if r == 0 else rnd(P * r * (1 + r) ** n / ((1 + r) ** n - 1))
    bal, rows, k = P, [], 0
    while bal > 0:
        k += 1
        interest = rnd(bal * r)
        due = min(bal + interest, emi + extra)
        if k == n and extra == 0:
            due = bal + interest
        rows.append((k, due, interest, due - interest))
        bal -= due - interest
    return emi, rows


def run(client, auth, loan=None, **body):
    body.setdefault("horizon_months", 12)
    body.setdefault("base_monthly_income", INC)
    body.setdefault("base_monthly_expense", EXP)
    if loan is not None:
        body["loan"] = loan
    r = client.post(f"{API}/simulations/run", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def flows(series):
    return [series[0]] + [b - a for a, b in zip(series, series[1:])]


# ------------------------------------------------------------------ 1. EMI, interest, totals (three standard examples)
@pytest.mark.parametrize("P,a,n,emi,total_int,total_paid,m1_int,m1_princ,last", [
    (5_000_000, 12, 12, 444_244, 330_927, 5_330_927, 50_000, 394_244, 444_243),
    (30_000_000, 9, 36, 953_992, 4_343_713, 34_343_713, 225_000, 728_992, 953_993),
    (100_000_000, 10, 120, 1_321_507, 58_580_912, 158_580_912, 833_333, 488_174, 1_321_579),
])
def test_three_standard_emi_examples(client, auth, P, a, n, emi, total_int, total_paid, m1_int, m1_princ, last):
    # hard-coded values (derived with exact fractions), and the independent schedule agrees with them
    ref_emi, rows = ref_schedule(P, a, n)
    assert (ref_emi, sum(r[2] for r in rows), sum(r[1] for r in rows), rows[0][2], rows[0][3], rows[-1][1]) == (emi, total_int, total_paid, m1_int, m1_princ, last)
    out = sim.loan_analysis(P, a, n, 0)
    assert (out["emi"], out["months"], out["total_interest"], out["total_paid"]) == (emi, n, total_int, total_paid)
    assert (out["first_12_months"][0]["interest"], out["first_12_months"][0]["principal"]) == (m1_int, m1_princ)
    q = client.get(f"{API}/simulations/loan", headers=auth, params={"principal": P, "annual_rate_pct": a, "tenure_months": n}).json()
    assert (q["emi"], q["total_interest"], q["total_paid"]) == (emi, total_int, total_paid)
    am = sim.amortize(P, a, n)
    assert am["schedule"][-1]["payment"] == last and am["schedule"][-1]["balance"] == 0
    assert sum(r["principal"] for r in am["schedule"]) == P and sum(r["payment"] for r in am["schedule"]) == total_paid


# ------------------------------------------------------------------ 2. zero interest
def test_zero_interest_repayment():
    out = sim.loan_analysis(120_000, 0, 12, 0)
    assert (out["emi"], out["months"], out["total_interest"], out["total_paid"]) == (10_000, 12, 0, 120_000)
    am = sim.amortize(100_000, 0, 3)
    assert [r["payment"] for r in am["schedule"]] == [33_333, 33_333, 33_334]       # the last payment absorbs the 1 paisa remainder
    assert am["total_interest"] == 0 and am["total_paid"] == 100_000 and all(r["interest"] == 0 for r in am["schedule"])


# ------------------------------------------------------------------ 3. Rs 10 lakh at 10% with Rs 5,000 extra a month
def test_ten_lakh_at_10_percent_with_5000_extra(client, auth):
    emi, rows = ref_schedule(**{"P": 100_000_000, "annual": 10, "n": 120}, extra=500_000)
    assert (emi, len(rows), sum(r[2] for r in rows), rows[-1][1]) == (EMI_10L, 74, 34_233_340, 1_263_329)   # independent values
    out = sim.loan_analysis(100_000_000, 10, 120, 500_000)
    e = out["with_extra_payment"]
    assert (e["months"], e["months_saved"], e["total_interest"], e["interest_saved"]) == (74, 46, 34_233_340, 58_580_912 - 34_233_340) and e["interest_saved"] == 24_347_572
    fast = sim.amortize(100_000_000, 10, 120, 500_000)["schedule"]
    assert all(r["payment"] == EMI_10L + 500_000 for r in fast[:-1])               # every payment but the last is EMI + extra
    assert fast[-1]["payment"] == 1_263_329 and fast[-1]["balance"] == 0            # last payment capped at balance + interest
    assert fast[-1]["payment"] == fast[-2]["balance"] + fast[-1]["interest"]


# ------------------------------------------------------------------ 4. existing loan vs new loan (same loan, no extra payment)
def test_existing_loan_does_not_deduct_the_emi_again(client, auth):
    s = run(client, auth, loan=dict(TEN_LAKH))                       # mode omitted -> existing (default)
    assert s["loan"]["mode"] == "existing" and s["loan"]["cash_flow"]["embedded_emi"] == EMI_10L
    assert s["series"]["baseline"] == s["series"]["scenario"] == [M * (i + 1) for i in range(12)]   # spending already includes the EMI
    assert s["difference"]["from_loan_cash_flow"] == s["difference"]["final_balance"] == 0


def test_new_loan_deducts_every_scheduled_payment_from_savings(client, auth):
    s = run(client, auth, loan={**TEN_LAKH, "mode": "new"})
    assert s["loan"]["mode"] == "new" and s["loan"]["cash_flow"]["embedded_emi"] == 0
    per_month = M - EMI_10L                                          # -321,507
    assert s["series"]["baseline"] == s["series"]["scenario"] == [per_month * (i + 1) for i in range(12)]
    assert s["baseline"]["final_balance"] == -3_858_084
    assert s["difference"]["final_balance"] == s["difference"]["from_loan_cash_flow"] == 0   # no extra payment -> no difference between the series
    assert s["baseline"]["monthly_savings"] == M                     # steady-state (non-loan) monthly savings is unchanged: loan flows are reported separately


def test_default_mode_is_existing_and_explicit_existing_is_identical(client, auth):
    a = run(client, auth, loan=dict(TEN_LAKH, extra_monthly_payment=500_000))
    b = run(client, auth, loan={**TEN_LAKH, "extra_monthly_payment": 500_000, "mode": "existing"})
    assert a == b


def test_extra_payment_in_both_modes_changes_the_difference_by_the_same_amount(client, auth):
    ex = run(client, auth, loan={**TEN_LAKH, "extra_monthly_payment": 500_000, "mode": "existing"})
    nw = run(client, auth, loan={**TEN_LAKH, "extra_monthly_payment": 500_000, "mode": "new"})
    pay = EMI_10L + 500_000
    assert flows(ex["series"]["scenario"]) == [M + EMI_10L - pay] * 12 == [500_000] * 12         # existing: only the extra comes out of savings
    assert flows(ex["series"]["baseline"]) == [M] * 12
    assert flows(nw["series"]["scenario"]) == [M - pay] * 12 == [-821_507] * 12                  # new: the whole payment comes out
    assert flows(nw["series"]["baseline"]) == [M - EMI_10L] * 12 == [-321_507] * 12
    for s in (ex, nw):
        d = s["difference"]
        assert d["monthly_savings"] == 0 and d["from_monthly_changes"] == 0                      # NOT a monthly-savings change
        assert d["from_loan_cash_flow"] == -500_000 * 12 == d["final_balance"] == -6_000_000     # loan-related cash flow, shown separately
        assert s["loan"]["cash_flow"]["net_vs_baseline"] == -6_000_000
        assert s["loan"]["cash_flow"]["scenario_extra"] == [500_000] * 12                         # extra tracked separately from the regular EMI
        assert s["loan"]["cash_flow"]["scenario_regular"] == [EMI_10L] * 12
        assert d["total_extra_saved"] == -6_000_000


# ------------------------------------------------------------------ 5. payoff month and the cash flow after payoff
def test_payoff_month_is_capped_and_the_freed_emi_is_added_back(client, auth):
    s = run(client, auth, horizon_months=80, loan={**TEN_LAKH, "extra_monthly_payment": 500_000})        # existing loan
    f = flows(s["series"]["scenario"])
    assert f[:73] == [500_000] * 73                                   # months 1-73: EMI + extra paid, extra comes out of savings
    assert f[73] == M + EMI_10L - 1_263_329 == 1_058_178              # month 74: capped final payment (+581.78 vs the usual EMI)
    assert f[74:] == [M + EMI_10L] * 6 == [2_321_507] * 6             # months 75-80: no repayments, the freed EMI is added back
    assert flows(s["series"]["baseline"]) == [M] * 80                 # baseline still paying its regular EMI inside its spending
    cf = s["loan"]["cash_flow"]
    assert cf["scenario_regular"][73] == 1_263_329 and cf["scenario_extra"][73] == 0     # last payment: all regular, no extra
    assert cf["scenario_regular"][74:] == [0] * 6 and cf["scenario_extra"][74:] == [0] * 6  # repayments stop after payoff
    assert cf["baseline_payments"] == [EMI_10L] * 80
    n = run(client, auth, horizon_months=80, loan={**TEN_LAKH, "extra_monthly_payment": 500_000, "mode": "new"})
    fn = flows(n["series"]["scenario"])
    assert fn[:73] == [M - 1_821_507] * 73 and fn[73] == M - 1_263_329 == -263_329 and fn[74:] == [M] * 6   # new loan: nothing deducted after payoff


# ------------------------------------------------------------------ 6. horizon longer than the loan tenure
def test_horizon_longer_than_tenure_interest_free_loan(client, auth):
    loan = dict(principal=1_200_000, annual_rate_pct=0, tenure_months=6)                      # EMI 200,000, 6 payments
    ex = run(client, auth, horizon_months=14, loan=loan)
    assert flows(ex["series"]["baseline"]) == [M] * 6 + [M + 200_000] * 8                     # freed EMI restored in the BASELINE too
    assert ex["series"]["baseline"] == ex["series"]["scenario"] and ex["baseline"]["final_balance"] == 6 * M + 8 * (M + 200_000) == 15_600_000
    nw = run(client, auth, horizon_months=14, loan={**loan, "mode": "new"})
    assert flows(nw["series"]["baseline"]) == [M - 200_000] * 6 + [M] * 8 and nw["baseline"]["final_balance"] == 12_800_000
    # with 1,000 extra a month the loan ends after 4 payments of 300,000; interest-free, so total repaid (and the final balance) is the same
    x = run(client, auth, horizon_months=14, loan={**loan, "extra_monthly_payment": 100_000})
    assert flows(x["series"]["scenario"]) == [M + 200_000 - 300_000] * 4 + [M + 200_000] * 10
    assert x["series"]["scenario"][3] == 4 * 900_000 < x["series"]["baseline"][3] == 4 * M
    assert x["scenario"]["final_balance"] == x["baseline"]["final_balance"] == 15_600_000 and x["difference"]["from_loan_cash_flow"] == 0


@pytest.mark.parametrize("mode", ["existing", "new"])
def test_horizon_beyond_tenure_loan_cash_flow_equals_interest_saved(client, auth, mode):
    s = run(client, auth, horizon_months=130, loan={**TEN_LAKH, "extra_monthly_payment": 500_000, "mode": mode})
    assert s["difference"]["from_loan_cash_flow"] == s["difference"]["final_balance"] == 24_347_572 == s["loan"]["with_extra_payment"]["interest_saved"]
    fb = flows(s["series"]["baseline"])
    embedded = EMI_10L if mode == "existing" else 0
    assert fb[119] == M + embedded - 1_321_579                       # baseline's capped last instalment (month 120)
    assert fb[120:] == [M + embedded] * 10                            # after the tenure: no more repayments in the baseline either


def test_difference_parts_always_add_up_with_a_loan_and_a_return(client, auth):
    s = run(client, auth, horizon_months=130, starting_savings=500_000, extra_monthly_saving=100_000, annual_return_pct=6,
            loan={**TEN_LAKH, "extra_monthly_payment": 500_000})
    d = s["difference"]
    assert d["from_monthly_changes"] == 100_000 * 130 and d["starting_savings"] == 500_000 and d["from_loan_cash_flow"] == 24_347_572
    assert d["final_balance"] == d["starting_savings"] + d["from_monthly_changes"] + d["from_loan_cash_flow"] + d["from_growth"]
    assert s["scenario"]["final_balance"] - s["baseline"]["final_balance"] == d["final_balance"]


@pytest.mark.parametrize("mode,embedded", [("existing", True), ("new", False)])
def test_series_match_an_independent_schedule(client, auth, mode, embedded):
    P, a, n, extra, h = 30_000_000, 9, 36, 200_000, 40
    emi, base_rows = ref_schedule(P, a, n)
    _, fast_rows = ref_schedule(P, a, n, extra)
    pb = [r[1] for r in base_rows] + [0] * h
    ps = [r[1] for r in fast_rows] + [0] * h
    s = run(client, auth, horizon_months=h, loan=dict(principal=P, annual_rate_pct=a, tenure_months=n, extra_monthly_payment=extra, mode=mode))
    emb = emi if embedded else 0
    assert flows(s["series"]["baseline"]) == [M + emb - pb[i] for i in range(h)]
    assert flows(s["series"]["scenario"]) == [M + emb - ps[i] for i in range(h)]
    assert s["difference"]["from_loan_cash_flow"] == sum(pb[:h]) - sum(ps[:h])


# ------------------------------------------------------------------ 7. validation and edge cases
BAD_LOANS = [
    ("principal zero", {**TEN_LAKH, "principal": 0}), ("principal negative", {**TEN_LAKH, "principal": -1}), ("principal above cap", {**TEN_LAKH, "principal": 10**13 + 1}),
    ("rate above 100", {**TEN_LAKH, "annual_rate_pct": 100.01}), ("rate negative", {**TEN_LAKH, "annual_rate_pct": -0.1}),
    ("tenure zero", {**TEN_LAKH, "tenure_months": 0}), ("tenure 601", {**TEN_LAKH, "tenure_months": 601}), ("tenure fractional", {**TEN_LAKH, "tenure_months": 12.5}),
    ("extra negative", {**TEN_LAKH, "extra_monthly_payment": -1}), ("unknown mode", {**TEN_LAKH, "mode": "maybe"}),
]


@pytest.mark.parametrize("label,loan", BAD_LOANS, ids=[b[0] for b in BAD_LOANS])
def test_invalid_loan_inputs_are_rejected_without_a_calculation(client, auth, label, loan):
    r = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 12, "base_monthly_income": INC, "base_monthly_expense": EXP, "loan": loan})
    assert r.status_code == 422, (label, r.text)


def test_rate_is_required_it_is_never_silently_zero(client, auth):
    loan = {k: v for k, v in TEN_LAKH.items() if k != "annual_rate_pct"}
    r = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 12, "base_monthly_income": INC, "base_monthly_expense": EXP, "loan": loan})
    assert r.status_code == 422


def test_boundary_values_are_accepted(client, auth):
    for loan in ({"principal": 100_000, "annual_rate_pct": 0, "tenure_months": 600}, {"principal": 100_000_000, "annual_rate_pct": 100, "tenure_months": 1},
                 {"principal": 100_000_000, "annual_rate_pct": 8.125, "tenure_months": 120}):
        assert client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 6, "base_monthly_income": INC, "base_monthly_expense": EXP, "loan": loan}).status_code == 200
    assert sim.loan_analysis(100_000_000, 8.125, 120, 0)["emi"] == 1_219_891


@pytest.mark.parametrize("P,a,n", [(1, 10, 12), (100, 7, 600), (10**13, 100, 600)])
def test_emi_that_rounds_to_zero_gives_a_clear_error(client, auth, P, a, n):
    loan = {"principal": P, "annual_rate_pct": a, "tenure_months": n}
    r = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 12, "base_monthly_income": INC, "base_monthly_expense": EXP, "loan": loan})
    assert r.status_code == 422 and "cannot be repaid in whole paise" in r.json()["detail"]
    q = client.get(f"{API}/simulations/loan", headers=auth, params={"principal": P, "annual_rate_pct": a, "tenure_months": n})
    assert q.status_code == 422 and "cannot be repaid in whole paise" in q.json()["detail"]
    with pytest.raises(ValueError, match="cannot be repaid in whole paise"):
        sim.loan_analysis(P, a, n, 0)


def test_loan_endpoint_limits(client, auth):
    ok = {"principal": 100_000_000, "annual_rate_pct": 10, "tenure_months": 120}
    assert client.get(f"{API}/simulations/loan", headers=auth, params={**ok, "extra_monthly_payment": 10**13 + 1}).status_code == 422
    assert client.get(f"{API}/simulations/loan", headers=auth, params={**ok, "extra_monthly_payment": -1}).status_code == 422
    assert client.get(f"{API}/simulations/loan", headers=auth, params={**ok, "tenure_months": 601}).status_code == 422
    assert client.get(f"{API}/simulations/loan", headers=auth, params={**ok, "annual_rate_pct": "nan"}).status_code == 422
    assert client.get(f"{API}/simulations/loan", headers=auth, params={**ok, "extra_monthly_payment": 10**9}).json()["with_extra_payment"]["months"] <= 2


@pytest.mark.parametrize("args", [(0, 10, 12, 0), (-5, 10, 12, 0), (100, -1, 12, 0), (100, 101, 12, 0), (100, 10, 0, 0), (100, 10, 601, 0), (100, 10, 12, -1), (100, float("nan"), 12, 0)])
def test_service_validation_rejects_bad_inputs(args):
    with pytest.raises(ValueError):
        sim.loan_analysis(*args)
