"""What this car has shown (analysis/vehicle_profile.py): measured,
inferred and predicted-against-actual, kept apart, and "too few to say"
where it is. All drives are synthetic, in temporary databases."""
from analysis.consumption_backtest import backtest
from analysis.energy_model import fit
from analysis.synthetic_journeys import journeys, samples
from analysis.vehicle_profile import build
from test_consumption_in_route import _feels_the_cold, _history
from test_route_planner import _client


def _check(ss, model=None, verdict="physics"):
    bt = backtest(ss)
    return {"samples": ss, "model": model, "backtest": dict(bt, verdict=verdict),
            "basis": "test basis"}


def test_measured_and_inferred_are_kept_apart_and_honest():
    ss = samples(journeys(200, seed=31))
    got = build(_check(ss, fit(ss)), 56.0)
    speeds = got["measured"]["by_speed"]
    assert all(("kwh_per_100km" in b) for b in speeds)
    town = speeds[0]
    assert town["enough"] and 10 < town["kwh_per_100km"] < 35
    inferred = {i["what"]: i for i in got["inferred"]}
    a, b = inferred["steady 120 km/h at 15 °C"], inferred["steady 80 km/h at 15 °C"]
    assert a["value"] > b["value"] and a["range_km"] < b["range_km"]
    assert inferred["steady 100 km/h at 0 °C"]["value"] > \
        inferred["steady 100 km/h at 15 °C"]["value"]
    assert all(i["sd"] >= 0 for i in got["inferred"])
    assert got["inferred_used"] and "Hills" in got["not_measured"][0]


def test_a_few_drives_say_too_few_and_their_inference_is_mostly_assumed():
    ss = samples(journeys(8, seed=32))
    got = build(_check(ss, fit(ss)), 56.0)
    assert any(not b["enough"] and b["kwh_per_100km"] is None
               for b in got["measured"]["by_speed"])
    assert any(i["mostly_assumed"] for i in got["inferred"])


def test_each_drive_is_set_against_what_was_predicted_before_it():
    ss = samples(journeys(120, seed=33))
    got = build(_check(ss, fit(ss)), 56.0)["predictions"]
    assert got["method"] == "physics" and got["drives"] >= 30
    row = got["rows"][0]
    assert row["arrival_error_pct"] == round(
        100 * (row["actual_kwh"] - row["predicted_kwh"]) / 56.0, 1) or \
        abs(row["arrival_error_pct"] - 100 * (row["actual_kwh"] - row["predicted_kwh"])
            / 56.0) < 0.15
    assert got["arrival_mae_pct"] >= 0 and row["ended"].endswith("+00:00")


def test_the_trips_page_and_its_api_show_it(tmp_path):
    repo, _ = _history(tmp_path / "t.db", _feels_the_cold)
    repo.close()
    client = _client(str(tmp_path / "t.db"))
    body = client.get("/trips", params={"days": 365}).text
    assert "What this car has shown" in body
    assert "Predicted before, measured after" in body
    api = client.get("/api/vehicle-profile")
    assert api.json()["drives"] == 100 and api.json()["measured"]["by_temperature"]
    assert "WVWZZZE1ZMS000001" not in api.text      # the profile carries no VIN
