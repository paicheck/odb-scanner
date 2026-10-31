"""En route: the plan judged against the charge the car reports, with the
phone's rules (android/.../route/Replan.kt). Nothing here changes a route."""
import pytest

from analysis.enroute import DRIFT_PCT, assess, expected_pct

# 300 km: start 90 %, arrive at the stop at km 100 with 20 % (0.7 % a km),
# leave with 80 %, arrive with 15 %. Reserve 10 %. One alternative 10 km
# before the stop, one 10 km after.
PLAN = {
    "start_pct": 90.0, "distance_km": 300.0, "arrival_pct": 15.0,
    "profile": [[0, 90.0], [100, 20.0], [100, 80.0], [300, 15.0]],
    "stops": [{"km": 100.0, "name": "Hub", "id": "1", "operator": "Op",
               "arrive_pct": 20.0, "depart_pct": 80.0,
               "alternatives": [{"km": 90.0, "name": "Before", "id": "2"},
                                {"km": 110.0, "name": "After", "id": "3"}]}],
}


def test_the_profile_is_read_like_the_phone():
    assert expected_pct(PLAN["profile"], 50) == pytest.approx(55.0)
    assert expected_pct(PLAN["profile"], 100) == 80.0      # a stop here counts as left
    assert expected_pct(PLAN["profile"], 400) == 15.0


def test_on_plan_is_normal():
    got = assess(PLAN, 50, 55.0)
    assert (got["level"], got["replan"]) == ("normal", False)
    assert got["projected_target_pct"] == pytest.approx(20.0)


def test_using_more_than_planned_widens_the_gap_with_distance():
    # At km 37.5 the plan expected 63.75 %; 31.5 % used against 26.25 is 1.2
    # times the plan. The 5.25 % gap becomes 14 % by the stop -- not 5.25
    # carried forward.
    got = assess(PLAN, 37.5, 58.5)
    assert got["ratio"] == pytest.approx(1.2)
    assert got["projected_target_pct"] == pytest.approx(58.5 - 1.2 * 43.75, abs=0.05)
    assert got["planned_target_pct"] - got["projected_target_pct"] == pytest.approx(
        14.0, abs=0.05)


def test_a_small_gap_with_the_stop_still_fine_is_a_warning():
    got = assess(PLAN, 90, 27.0 - DRIFT_PCT - 0.5)        # about 16 % at the stop
    assert got["level"] == "warning" and got["replan"] is False


def test_a_tight_stop_says_adjust_and_re_plan():
    got = assess(PLAN, 50, 51.0)                         # about 12 % at the stop
    assert got["level"] == "adjust" and got["replan"] is True
    assert "Re-plan" in got["action"]


def test_out_of_reach_is_critical_with_the_known_chargers_within_reach():
    got = assess(PLAN, 60, 40.0)                         # reach: about 36 km
    assert got["level"] == "critical" and got["projected_target_pct"] < 10
    assert got["reach_km"] == pytest.approx(36.0, abs=0.5)
    names = [a["name"] for a in got["alternatives"]]
    assert "Before" in names and "Hub" not in names and "After" not in names
    assert "never known" in got["note"]                 # availability is not claimed


def test_a_stale_reading_is_not_judged():
    got = assess(PLAN, 60, 20.0, reading_age_s=3600)
    assert got["level"] == "normal" and got["stale_reading"] is True
    assert "deviation_pct" not in got


def test_at_a_planned_stop_the_jump_in_the_profile_means_nothing():
    got = assess(PLAN, 99, 19.0)
    assert got["level"] == "normal" and got["at_stop"] == 0


def test_after_charging_the_ratio_is_not_measured():
    got = assess(PLAN, 50, 51.0, charged_since_plan=True)
    assert got["ratio"] == 1.0


def test_above_plan_suggests_checking_whether_a_stop_can_go():
    got = assess(PLAN, 50, 61.0)
    assert got["level"] == "normal" and got["replan"] is True
    assert "may no longer be needed" in got["action"]


def test_the_old_cases_still_hold():
    assert assess(PLAN, 50, 55.0)["level"] == "normal"
    assert assess(PLAN, 50, 40.0)["level"] in ("adjust", "critical")


def test_a_real_plan_from_the_planner_is_read_as_planned():
    from routing.planner import CarModel, Charger, PlanSettings, plan_trip
    from test_route_planner import _north
    road = _north(400)
    chargers = [Charger(f"C{k}", road[k][0], 0.0, 150, id=str(k))
                for k in range(50, 400, 25)]
    plan = plan_trip(road, 15000, 90, CarModel(53.2, 0.17), PlanSettings(),
                     chargers).as_dict()
    first = plan["stops"][0]["km"]
    on_plan = expected_pct(plan["profile"], first / 2)
    assert assess(plan, first / 2, on_plan)["level"] == "normal"
    low = assess(plan, first / 2, on_plan - 25)
    # The plan knows only the chargers near its stops, none of them in reach.
    assert low["level"] == "critical" and low["alternatives"] == []
    assert "re-plan to search" in low["action"]
    nearer = assess(plan, first - 40, expected_pct(plan["profile"], first - 40) - 6)
    assert nearer["level"] == "critical" and nearer["alternatives"]
    reach = first - 40 + nearer["reach_km"]
    assert all(first - 40 < a["km"] <= reach for a in nearer["alternatives"])


def test_being_on_a_tight_plan_is_not_a_problem():
    tight = dict(PLAN, profile=[[0, 90.0], [100, 11.0], [100, 80.0], [300, 15.0]])
    tight["stops"] = [dict(PLAN["stops"][0], arrive_pct=11.0)]
    on = expected_pct(tight["profile"], 50)
    assert assess(tight, 50, on)["level"] == "normal"
    assert assess(tight, 50, on - 1.0)["level"] == "normal"      # noise, not drift
    assert assess(tight, 50, on - 4.0)["level"] == "critical"
