"""What-if simulation: baseline vs scenario comparison.

Seeded history (see tests.conftest.make_statement, 4 months): average income 10,000,000 paise, spending 3,837,400,
so the baseline saves 6,162,600 paise a month; a 20% cut of Food is worth 31,500 a month. All expectations below are
worked out by hand from those figures, not read back from the code under test.
"""
from tests.conftest import API
from tests.test_finance_modules import seed

BASE_MONTHLY = 6_162_600
FOOD_20 = 31_500


def run(client, auth, **body):
    body.setdefault("horizon_months", 12)
    r = client.post(f"{API}/simulations/run", headers=auth, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def set_profile_savings(client, auth, paise):
    assert client.patch(f"{API}/profile", headers=auth, json={"current_savings": paise}).status_code == 200


def test_scenario_starting_savings_5000_over_24_months_with_baseline_zero(client, auth):
    """The reported case: baseline starts at 0 (profile), the scenario starts with Rs 5,000."""
    seed(client, auth)
    s = run(client, auth, horizon_months=24, starting_savings=500_000)
    assert (s["baseline_starting_savings"], s["scenario_starting_savings"]) == (0, 500_000)
    assert s["baseline"]["final_balance"] == BASE_MONTHLY * 24
    assert s["scenario"]["final_balance"] == BASE_MONTHLY * 24 + 500_000
    d = s["difference"]
    assert d["final_balance"] == 500_000                      # the headline difference is exactly Rs 5,000
    assert d["starting_savings"] == 500_000                   # ... and it is attributed to starting savings
    assert d["monthly_savings"] == 0 and d["from_monthly_changes"] == 0 and d["total_extra_saved"] == 0  # NOT extra monthly saving
    assert d["from_growth"] == 0                              # no return assumed
    assert s["scenario"]["monthly_savings"] == s["baseline"]["monthly_savings"]
    # chart series: the scenario line sits exactly Rs 5,000 above the baseline in every one of the 24 months
    assert len(s["series"]["baseline"]) == len(s["series"]["scenario"]) == 24
    assert all(b - a == 500_000 for a, b in zip(s["series"]["baseline"], s["series"]["scenario"]))
    assert s["series"]["baseline"][0] == BASE_MONTHLY and s["series"]["scenario"][0] == BASE_MONTHLY + 500_000


def test_baseline_uses_the_profile_balance_and_the_input_only_changes_the_scenario(client, auth):
    seed(client, auth)
    set_profile_savings(client, auth, 200_000)
    s = run(client, auth, horizon_months=12, starting_savings=700_000)
    assert (s["baseline_starting_savings"], s["scenario_starting_savings"]) == (200_000, 700_000)
    assert s["series"]["baseline"][0] == 200_000 + BASE_MONTHLY      # baseline = profile, untouched by the input
    assert s["series"]["scenario"][0] == 700_000 + BASE_MONTHLY
    assert s["difference"]["final_balance"] == s["difference"]["starting_savings"] == 500_000
    # without an input both lines start from the profile and there is no difference at all
    same = run(client, auth, horizon_months=12)
    assert same["baseline_starting_savings"] == same["scenario_starting_savings"] == 200_000
    assert same["series"]["baseline"] == same["series"]["scenario"]
    assert same["difference"]["final_balance"] == same["difference"]["starting_savings"] == same["difference"]["from_monthly_changes"] == 0
    # a LOWER scenario balance is a negative difference, not clamped or hidden
    low = run(client, auth, horizon_months=12, starting_savings=50_000)
    assert low["difference"]["starting_savings"] == -150_000 == low["difference"]["final_balance"]


def test_extra_monthly_saving_is_reported_as_monthly_not_as_starting_savings(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=12, extra_monthly_saving=300_000)
    d = s["difference"]
    assert d["monthly_savings"] == 300_000
    assert d["final_balance"] == d["from_monthly_changes"] == d["total_extra_saved"] == 300_000 * 12
    assert d["starting_savings"] == 0 and d["from_growth"] == 0
    assert s["baseline_starting_savings"] == s["scenario_starting_savings"]


def test_income_changes_flow_through_the_comparison(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=6, income_increase_amount=100_000, income_increase_pct=10)
    extra = 100_000 + 1_000_000                                    # Rs 1,000 + 10% of 100,000 income
    assert s["scenario"]["monthly_income"] == 10_000_000 + extra
    d = s["difference"]
    assert d["monthly_savings"] == extra and d["final_balance"] == d["from_monthly_changes"] == extra * 6
    assert d["starting_savings"] == 0


def test_category_reduction_flows_through_the_comparison(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=12, category_cuts=[{"category": "Food", "percent": 20}])
    assert s["scenario"]["category_cuts"][0]["monthly_saving"] == FOOD_20
    assert s["scenario"]["monthly_expense"] == 3_837_400 - FOOD_20
    d = s["difference"]
    assert d["monthly_savings"] == FOOD_20 and d["final_balance"] == d["from_monthly_changes"] == FOOD_20 * 12
    assert d["starting_savings"] == 0


def test_all_levers_together_are_decomposed_and_sum_to_the_headline_difference(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=24, starting_savings=500_000, extra_monthly_saving=100_000, income_increase_amount=50_000,
            category_cuts=[{"category": "Food", "percent": 20}])
    d = s["difference"]
    monthly = 100_000 + 50_000 + FOOD_20
    assert d["starting_savings"] == 500_000
    assert d["monthly_savings"] == monthly and d["from_monthly_changes"] == monthly * 24
    assert d["from_growth"] == 0
    assert d["final_balance"] == 500_000 + monthly * 24 == d["starting_savings"] + d["from_monthly_changes"] + d["from_growth"]


def test_with_an_illustrative_return_the_growth_part_is_separate_and_the_parts_still_sum(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=24, starting_savings=500_000, extra_monthly_saving=100_000, annual_return_pct=12)
    d = s["difference"]
    assert d["starting_savings"] == 500_000 and d["from_monthly_changes"] == 100_000 * 24
    assert d["from_growth"] > 0                                   # growth on the extra money only (the baseline grows too)
    assert d["final_balance"] == d["starting_savings"] + d["from_monthly_changes"] + d["from_growth"]
    assert s["scenario"]["final_balance"] - s["baseline"]["final_balance"] == d["final_balance"]


def test_goal_and_emergency_fund_timing_use_the_scenario_balance(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=12, starting_savings=5_000_000, goal_target=20_000_000, emergency_fund_months=6)
    # baseline: 6,162,600 a month -> Rs 200,000 target reached in month 4; scenario starts with Rs 50,000 -> month 3
    assert (s["goal"]["baseline_month"], s["goal"]["scenario_month"]) == (4, 3)
    ef_target = 3_837_400 * 6
    assert s["emergency_fund"]["baseline_target"] == s["emergency_fund"]["scenario_target"] == ef_target
    assert s["emergency_fund"]["baseline_month"] == 4                                 # 4 x 6,162,600 >= 23,024,400
    assert s["emergency_fund"]["scenario_month"] == 3                                 # 5,000,000 + 3 x 6,162,600 >= 23,024,400


def test_saved_scenario_keeps_the_new_difference_fields(client, auth):
    seed(client, auth)
    s = run(client, auth, horizon_months=12, starting_savings=500_000, save=True, name="With 5k")
    assert s["id"]
    items = client.get(f"{API}/simulations", headers=auth).json()["items"]
    assert items and items[0]["summary"]["difference"]["starting_savings"] == 500_000
