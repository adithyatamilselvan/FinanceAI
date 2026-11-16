"""Illustrative investment growth applies ONLY to positive savings balances.

12% a year is exactly 1% a month, so every expected value below is worked out by hand (growth = round-half-up(opening balance x 1%)).
"""
import random
from decimal import ROUND_HALF_UP, Decimal

from app.services.simulation import accumulate
from tests.conftest import API

R12 = 12  # annual %, i.e. 1% per month


# ---------------------------------------------------------------- unit tests on the projection itself
def test_positive_balance_grows_monthly():
    # 100,000 -> +1,000 -> +1,010 -> +1,020.1 (rounds to 1,020)
    assert accumulate(100_000, [0, 0, 0], R12) == [101_000, 102_010, 103_030]
    # growth is earned on the opening balance, contributions are added after: 100,000+1,000+50,000 = 151,000; then +1,510 +50,000
    assert accumulate(100_000, [50_000, 50_000], R12) == [151_000, 202_510]


def test_growth_rounds_half_up_like_every_other_money_figure():
    assert accumulate(150, [0], R12) == [152]   # 1.5 -> 2
    assert accumulate(50, [0], R12) == [51]     # 0.5 -> 1
    assert accumulate(49, [0], R12) == [49]     # 0.49 -> 0


def test_zero_balance_earns_nothing():
    assert accumulate(0, [0, 0, 0], R12) == [0, 0, 0]
    assert accumulate(0, [-1_000], R12) == [-1_000]          # zero opening balance: no growth, goes negative by the contribution only


def test_negative_balance_is_never_grown():
    # the reported symptom: a negative balance must NOT become more negative because of an investment return
    assert accumulate(-100_000, [0, 0, 0], R12) == [-100_000, -100_000, -100_000]
    assert accumulate(-100_000, [-5_000, -5_000], R12) == [-105_000, -110_000]
    flows = [-23_000] * 24
    assert accumulate(0, flows, 6) == accumulate(0, flows, 0) == [-23_000 * (i + 1) for i in range(24)]
    assert accumulate(-559_836, [0] * 24, 6) == [-559_836] * 24          # the balance from the bug report stays exactly the same


def test_negative_to_positive_growth_starts_only_after_the_balance_is_positive():
    # -10,000 +6,000/month: m1 -4,000 (no growth), m2 +2,000 (no growth on the negative opening), then growth on the POSITIVE balance only:
    # m3: 2,000 + round(20) + 6,000 = 8,020 ; m4: 8,020 + round(80.2)=80 + 6,000 = 14,100
    assert accumulate(-10_000, [6_000] * 4, R12) == [-4_000, 2_000, 8_020, 14_100]
    # without a return the same flows give the plain running total: growth added exactly 20 + 80 = 100, and only from month 3
    plain = accumulate(-10_000, [6_000] * 4, 0)
    assert plain == [-4_000, 2_000, 8_000, 14_000]
    assert [a - b for a, b in zip(accumulate(-10_000, [6_000] * 4, R12), plain)] == [0, 0, 20, 100]


def test_positive_to_negative_stops_growing_once_the_balance_is_not_positive():
    # 10,000 -6,000/month: m1 10,000+100-6,000 = 4,100 ; m2 4,100+41-6,000 = -1,859 ; then no growth: -7,859, -13,859
    assert accumulate(10_000, [-6_000] * 4, R12) == [4_100, -1_859, -7_859, -13_859]


def test_zero_return_is_a_plain_running_total_and_positive_results_match_the_original_formula():
    assert accumulate(5_000, [100, -50, 70], 0) == [5_100, 5_050, 5_120]
    # regression: for positive balances the result is identical to the previous implementation, round(bal*(1+r)) + contribution
    rng = random.Random(7)
    for _ in range(300):
        start = rng.randint(1, 5_000_000)
        flows = [rng.randint(0, 400_000) for _ in range(rng.randint(1, 36))]  # never negative, so the balance stays positive
        pct = rng.choice([1, 4.5, 6, 9, 12, 18])
        r = Decimal(str(pct)) / Decimal(1200)
        bal, old = start, []
        for m in flows:
            bal = int((Decimal(bal) * (1 + r)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) + m
            old.append(bal)
        assert accumulate(start, flows, pct) == old


# ---------------------------------------------------------------- through the API (baseline and scenario use the same rule)
def run(client, auth, **body):
    r = client.post(f"{API}/simulations/run", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_negative_baseline_with_6_percent_return_is_identical_to_no_return_24_months(client, auth):
    """The reported case: 24 months, 6% illustrative return, nothing else changed, balance negative throughout."""
    base = dict(horizon_months=24, base_monthly_income=10_000_000, base_monthly_expense=10_023_000)   # saves -23,000 paise a month
    plain = run(client, auth, **base)
    grown = run(client, auth, **base, annual_return_pct=6)
    assert plain["baseline"]["final_balance"] == -23_000 * 24
    assert grown["series"]["baseline"] == plain["series"]["baseline"] == [-23_000 * (i + 1) for i in range(24)]
    assert grown["series"]["scenario"] == plain["series"]["scenario"]
    assert grown["baseline"]["final_balance"] == grown["scenario"]["final_balance"] == -23_000 * 24     # never more negative
    d = grown["difference"]
    assert d["final_balance"] == d["from_growth"] == d["from_monthly_changes"] == d["starting_savings"] == 0
    assert grown["negative_balance_months"] == {"baseline": 24, "scenario": 24}
    assert any("only on a positive savings balance" in a for a in grown["assumptions"])


def test_baseline_negative_and_scenario_positive_each_follow_the_same_rule(client, auth):
    base = dict(horizon_months=24, base_monthly_income=10_000_000, base_monthly_expense=10_050_000)   # baseline: -50,000 a month
    s = run(client, auth, **base, extra_monthly_saving=80_000, annual_return_pct=12)                  # scenario: +30,000 a month
    assert s["series"]["baseline"] == [-50_000 * (i + 1) for i in range(24)]                          # negative: no growth, ever
    bal, expect = 0, []
    for _ in range(24):                                                                              # independent model of the positive scenario
        growth = int((Decimal(bal) * Decimal("0.01")).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if bal > 0 else 0
        bal = bal + growth + 30_000
        expect.append(bal)
    assert s["series"]["scenario"] == expect and expect[-1] > 30_000 * 24                             # the positive balance did grow
    d = s["difference"]
    assert d["from_monthly_changes"] == 80_000 * 24 and d["from_growth"] == expect[-1] - 30_000 * 24 > 0
    assert d["final_balance"] == d["starting_savings"] + d["from_monthly_changes"] + d["from_growth"]
    assert s["negative_balance_months"] == {"baseline": 24, "scenario": 0}


def test_positive_balance_behaviour_through_the_api_is_unchanged(client, auth):
    s = run(client, auth, horizon_months=12, base_monthly_income=10_000_000, base_monthly_expense=9_000_000, annual_return_pct=12, starting_savings=1_000_000)
    r = Decimal("0.01")
    bal, expect = 1_000_000, []
    for _ in range(12):
        bal = int((Decimal(bal) * (1 + r)).quantize(Decimal(1), rounding=ROUND_HALF_UP)) + 1_000_000   # the original formula
        expect.append(bal)
    assert s["series"]["scenario"] == expect
    assert s["negative_balance_months"] == {"baseline": 0, "scenario": 0}
