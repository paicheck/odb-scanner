"""Chart semantics: units, axes, precision, gaps, provenance and the battery /
range / charging pages.

Every fixture is SYNTHETIC and lives in a throwaway database; nothing here reads
the real vehicle database or talks to a vehicle. Each test pins one failure mode
found in the chart audit (docs/investigation/chart_semantics/progress.md, F1-F12)
or one rule the charts must keep.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import chart_semantics as cs  # noqa: E402

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _q(key):
    from decoders.quality import quality_for
    return quality_for("meb", key)


# ------------------------------------------------------ 1. units and axes
def test_unit_labels_are_display_only():
    assert cs.unit_label("degC") == "°C"
    assert cs.unit_label("kWh/100km") == "kWh/100 km"
    assert cs.unit_label("V") == "V"
    assert cs.unit_label(None) == ""
    assert cs.unit_label("code") == ""


def test_axis_policy_per_unit_and_key():
    assert cs.policy_for("soc_abs", "%") == (cs.BOUNDED, (0.0, 100.0))
    assert cs.policy_for("pack_current", "A")[0] == cs.SIGNED
    assert cs.policy_for("pack_power_kw", "kW")[0] == cs.SIGNED
    assert cs.policy_for("cell_delta_mv", "mV")[0] == cs.ZERO_BASED
    assert cs.policy_for("range_displayed", "km")[0] == cs.ZERO_BASED
    # Same unit, different meaning: a counter is never forced to start at zero.
    assert cs.policy_for("odometer_km", "km")[0] == cs.CUMULATIVE
    assert cs.policy_for("pack_voltage", "V")[0] == cs.AUTO
    axis = cs.axis_for("battery_temp", "degC", [10.0, 12.0])
    assert axis["unit_label"] == "°C" and axis["group"] == "degC"
    assert "does not start at zero" in axis["note"]


# --------------------------------------------- 2. compatible unit grouping
def _series(key, unit, values):
    return cs.build_series(key, [(_iso(T0 + timedelta(minutes=i)), v, "reported")
                                 for i, v in enumerate(values)],
                           name=key, unit=unit, quality=_q(key), gap_s=900)


def test_incompatible_units_are_split_into_separate_panels():
    series = {"pack_voltage": _series("pack_voltage", "V", [400, 401]),
              "pack_current": _series("pack_current", "A", [-10, 5])}
    panels = cs.assemble_panels(
        [{"id": "p", "title": "Pack", "keys": ["pack_voltage", "pack_current"]}],
        series)
    assert len(panels) == 2
    assert {tuple(p["left"]) for p in panels} == {("pack_voltage",), ("pack_current",)}
    assert all(p["right"] == [] for p in panels)
    assert any("(V)" in p["title"] for p in panels)


def test_same_unit_shares_one_axis_and_dual_needs_explicit_request():
    series = {"soc_abs": _series("soc_abs", "%", [70, 71]),
              "soc_normal": _series("soc_normal", "%", [72, 73]),
              "pack_voltage": _series("pack_voltage", "V", [400, 401])}
    same = cs.assemble_panels(
        [{"id": "s", "title": "SoC", "keys": ["soc_abs", "soc_normal"]}], series)
    assert len(same) == 1 and same[0]["left"] == ["soc_abs", "soc_normal"]
    dual = cs.assemble_panels(
        [{"id": "d", "title": "x", "keys": ["soc_abs", "pack_voltage"],
          "dual": True}], series)
    assert len(dual) == 1
    assert dual[0]["left"] == ["soc_abs"] and dual[0]["right"] == ["pack_voltage"]
    assert dual[0]["right_axis"]["unit_label"] == "V"


def test_panel_axis_spans_every_series_so_toggling_does_not_rescale():
    series = {"soc_abs": _series("soc_abs", "%", [70, 71]),
              "soc_normal": _series("soc_normal", "%", [88, 90])}
    p = cs.assemble_panels(
        [{"id": "s", "title": "SoC", "keys": ["soc_abs", "soc_normal"]}], series)[0]
    r = p["left_axis"]["range"]
    assert r["min"] <= 70 and r["max"] >= 90


# ---------------------------------------- 3. percentage and signed scales
def test_percentage_axis_fits_data_inside_valid_range():
    r = cs.axis_range([68.4, 93.4], cs.BOUNDED, (0.0, 100.0))
    assert 0.0 <= r["min"] <= 68.4 and 93.4 <= r["max"] <= 100.0
    # Not stretched to the full 0-100 (that would flatten a 25-point swing).
    assert r["min"] > 0
    full = cs.axis_range([99.0, 100.0], cs.BOUNDED, (0.0, 100.0))
    assert full["max"] == 100.0


def test_signed_axis_always_shows_zero():
    neg = cs.axis_range([-64.2, -0.4], cs.SIGNED)
    assert neg["min"] <= -64.2 and neg["max"] >= 0.0
    both = cs.axis_range([-20.0, 35.0], cs.SIGNED)
    assert both["min"] <= -20 and both["max"] >= 35


def test_cumulative_axis_is_not_forced_to_zero():
    r = cs.axis_range([111005.0, 111019.0], cs.CUMULATIVE)
    assert r["min"] > 110000


def test_zero_based_axis_starts_at_zero():
    r = cs.axis_range([12.9, 18.1], cs.ZERO_BASED)
    assert r["min"] == 0.0 and r["max"] >= 18.1


# ----------------------------------------- 4. precision and formatting
def test_precision_follows_source_resolution():
    assert cs.decimals_for("cell_voltage_max", "V") == 3
    assert cs.decimals_for("cell_v_042", "V") == 3
    assert cs.decimals_for("pack_voltage", "V") == 1
    assert cs.decimals_for("odometer_km", "km") == 0
    assert cs.decimals_for("lv_voltage_obd", "V") == 2


def test_tick_decimals_match_the_step():
    assert cs.axis_range([3.85, 4.09], cs.AUTO)["tick_decimals"] == 2
    assert cs.axis_range([418.0, 441.5], cs.AUTO)["tick_decimals"] == 0


def test_template_number_and_time_filters():
    from web.dashboard import _format_number, _format_ts

    assert _format_number(None, 2) == "—"
    assert _format_number(0, 1) == "0.0"          # a real zero is shown
    assert _format_number(3.98765, 3) == "3.988"
    assert _format_ts("2026-10-24T14:51:40+00:00") == "2026-10-24 14:51:40 UTC"
    # A non-UTC offset is kept, never relabelled as UTC.
    assert "+02:00" in _format_ts("2026-10-24T14:51:40+02:00")
    assert _format_ts(None) == "—"


# --------------------------------- 5. nulls, missing, true zeroes, empty
def test_empty_series_is_not_a_series_of_zeroes():
    empty = cs.raw_stats([])
    zeros = cs.raw_stats([0.0, 0.0, 0.0])
    assert empty["n"] == 0 and empty["min"] is None and empty["mean"] is None
    assert zeros["n"] == 3 and zeros["min"] == 0.0 and zeros["mean"] == 0.0
    assert cs.axis_range([], cs.AUTO) is None
    assert cs.axis_range([None, "P"], cs.AUTO) is None


def test_constant_zero_gets_a_visible_axis():
    signed = cs.axis_range([0.0, 0.0], cs.SIGNED)
    assert signed["min"] < 0 < signed["max"]
    zero = cs.axis_range([0.0], cs.ZERO_BASED)
    assert zero["min"] == 0.0 and zero["max"] > 0


def test_nulls_and_text_are_holes_not_zero():
    s = cs.build_series(
        "pack_voltage",
        [(_iso(T0), 400.0, "reported"), (_iso(T0 + timedelta(minutes=1)), None,
                                         "reported"),
         (_iso(T0 + timedelta(minutes=2)), "ERR", "reported")],
        name="v", unit="V", quality=_q("pack_voltage"), gap_s=900)
    assert s["value"] == [400.0]
    assert s["stats"]["n"] == 1 and s["stats"]["non_numeric"] == 2


# --------------------------------------------------------------- 6. gaps
def test_coverage_counts_gaps_wider_than_threshold():
    ts = [_iso(T0), _iso(T0 + timedelta(minutes=5)),
          _iso(T0 + timedelta(hours=3)), _iso(T0 + timedelta(hours=3, minutes=5))]
    cov = cs.coverage(ts, gap_s=900)
    assert cov["gaps"] == 1
    assert cov["largest_gap_s"] == pytest.approx(3 * 3600 - 300)
    assert cov["first_ts"] == ts[0] and cov["last_ts"] == ts[-1]


# ------------------------------------------- 7. duplicates and ordering
@pytest.fixture()
def repo(tmp_path):
    from database.repository import Repository
    r = Repository(tmp_path / "charts.db")
    r.vehicle_id = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    yield r
    r.close()


def _rec(repo, key, ts, value, *, unit="V", provenance="reported", ecu="bat_mgmt"):
    repo.record_measurement(repo.vehicle_id, ts, key, ecu, "22", "F1A1", unit,
                            provenance, "identity", "experimentally determined",
                            "AB", value=value, success=True)


def test_duplicate_timestamps_are_kept_in_insertion_order(repo):
    ts = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    for v in (3.0, 1.0, 2.0):
        _rec(repo, "pack_voltage", ts, v)
    rows = repo.measurement_series(repo.vehicle_id, "pack_voltage", 1)
    assert [r["value"] for r in rows] == [3.0, 1.0, 2.0]
    many = repo.measurement_series_many(repo.vehicle_id, ["pack_voltage"], 1)
    assert [r["value"] for r in many] == [3.0, 1.0, 2.0]
    assert cs.coverage([r["ts"] for r in rows], 900)["duplicate_timestamps"] == 2


# ------------------------------------ 8. original timestamps preserved
def test_series_keeps_original_timestamps():
    ts = [_iso(T0 + timedelta(seconds=s)) for s in (0, 7, 61, 3600)]
    s = cs.build_series("pack_voltage", [(t, 400.0 + i, "reported")
                                         for i, t in enumerate(ts)],
                        name="v", unit="V", quality=_q("pack_voltage"), gap_s=900)
    assert s["ts"] == ts


def test_pairing_keeps_both_timestamps_and_signed_offset():
    a = [(_iso(T0), 70.0), (_iso(T0 + timedelta(minutes=10)), 69.0)]
    b = [(_iso(T0 + timedelta(seconds=30)), 210.0),
         (_iso(T0 + timedelta(minutes=10, seconds=-20)), 205.0)]
    pairs = cs.pair_nearest(a, b, tolerance_s=60)
    assert [p["dt_s"] for p in pairs] == [30.0, -20.0]
    assert pairs[0]["ts_a"] == a[0][0] and pairs[0]["ts_b"] == b[0][0]
    # Outside the tolerance nothing is paired (and nothing interpolated).
    assert cs.pair_nearest(a, b, tolerance_s=10) == []


# --------------------------------------------------- 9. provenance
def test_provenance_survives_downsampling_and_is_counted():
    rows = [(_iso(T0 + timedelta(seconds=i)), 400.0 + (i % 7), "reported")
            for i in range(3000)]
    rows[1500] = (rows[1500][0], 480.0, "imported: Car Scanner CSV")
    s = cs.build_series("pack_voltage", rows, name="v", unit="V",
                        quality=_q("pack_voltage"), gap_s=900, max_points=200)
    assert s["downsampled"] is True
    assert s["provenance_counts"] == {"reported": 2999, "imported": 1}
    by_ts = {r[0]: cs.provenance_kind(r[2]) for r in rows}
    assert all(by_ts[t] == p for t, p in zip(s["ts"], s["provenance"]))
    # The imported spike is an extreme, so min/max sampling keeps it.
    assert "imported" in s["provenance"]


def test_provenance_kinds():
    assert cs.provenance_kind("imported: Car Scanner CSV") == "imported"
    assert cs.provenance_kind("simulated") == "simulated"
    assert cs.provenance_kind("calculated") == "calculated"
    assert cs.provenance_kind(None) == "unknown"


# ------------------------------------------- 13/14. outliers, downsampling
def test_outliers_are_flagged_not_removed():
    s = _series("pack_voltage", "V", [430.0, 1738.0, 431.0])
    assert 1738.0 in s["value"]
    assert s["plausibility"]["outside"] == 1
    assert s["plausibility"]["range"] == [200.0, 500.0]
    assert s["out_of_range"] == [False, True, False]


def test_key_without_documented_range_is_not_flagged():
    s = _series("range_displayed", "km", [210.0, 99999.0])
    assert s["plausibility"]["range"] is None
    assert s["plausibility"]["outside"] == 0


def test_stats_use_raw_values_not_the_downsampled_points():
    rows = [(_iso(T0 + timedelta(seconds=i)), 10.0, "reported") for i in range(2000)]
    rows[999] = (rows[999][0], 99.0, "reported")
    s = cs.build_series("cell_delta_mv", rows, name="d", unit="mV",
                        quality=_q("cell_delta_mv"), gap_s=900, max_points=50)
    assert s["returned_points"] <= 52
    assert 99.0 in s["value"]                      # spike survives
    assert s["stats"]["n"] == 2000                 # raw count, not 50
    assert s["stats"]["mean"] == pytest.approx((1999 * 10.0 + 99.0) / 2000)


# ----------------------------------------------- 10. battery cell sweeps
def test_cell_sweeps_keep_cell_identity_and_count_missing():
    from analysis.battery import cell_sweeps

    rows = [(_iso(T0), 0, 3.900), (_iso(T0), 1, 3.950), (_iso(T0), 2, 3.920)]
    out = cell_sweeps(rows, expected_cells=4)
    sw = out["sweeps"][0]
    assert sw["min_cell"] == 1 and sw["min_index"] == 0      # 1-based number
    assert sw["max_cell"] == 2 and sw["spread_mv"] == 50.0
    assert sw["missing"] == 1
    assert out["latest"]["missing_numbers"] == [4]
    assert cell_sweeps([], expected_cells=4)["latest"] is None


# ------------------------------------------ 12. distance derivation
def test_counter_delta_states():
    ok = cs.counter_delta([("a", 100.0), ("b", 104.5), ("c", 110.0)])
    assert ok["status"] == "ok" and ok["delta"] == 10.0
    assert cs.counter_delta([("a", 100.0)])["status"] == "insufficient"
    assert cs.counter_delta([("a", 100.0), ("b", 90.0)])["status"] == "inconsistent"
    assert cs.counter_delta([("a", 100.0), ("b", 90.0)])["delta"] is None


# ------------------------------------------------------- web fixtures
def _make_app(tmp_path, populate):
    from config import Config
    from database.repository import Repository
    from web.dashboard import create_app

    db = tmp_path / "web_charts.db"
    cfg = Config({
        "database": {"path": str(db)},
        "vehicle": {"did_profile": "meb"},
        "web": {"host": "127.0.0.1"},
        "collector": {"max_value_age_s": 900, "slow_poll_interval": 60,
                      "poll_interval": 5},
        "analysis": {"trend_window_days": 30, "charging_window_days": 90},
    })
    r = Repository(str(db))
    r.vehicle_id = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    extra = populate(r) if populate else {}
    r.close()
    from fastapi.testclient import TestClient
    return TestClient(create_app(cfg, bind_host="127.0.0.1")), str(db), extra


def _populate(r):
    now = datetime.now(timezone.utc)
    for i in range(6):
        ts = _iso(now - timedelta(minutes=60 - i * 5))
        _rec(r, "soc_abs", ts, 70.0 + i, unit="%")
        _rec(r, "soc_normal", ts, 72.0 + i, unit="%")
        _rec(r, "pack_voltage", ts, 430.0 + i)
        _rec(r, "pack_current", ts, -10.0 - i, unit="A")
        _rec(r, "range_displayed", _iso(now - timedelta(minutes=60 - i * 5,
                                                        seconds=-20)),
             200.0 - i, unit="km")
        _rec(r, "odometer_km", ts, 111000.0 + i, unit="km")
        _rec(r, "lv_voltage_obd", ts, 14.1, unit="V", ecu="obd")
        r.record_cell_voltages(r.vehicle_id, ts, [3.90, 3.95, 3.92])
    _rec(r, "soc_abs", _iso(now - timedelta(minutes=2)), 75.5, unit="%",
         provenance="imported: Car Scanner CSV")
    sid = r.open_session(r.vehicle_id, _iso(now - timedelta(hours=3)), "AC",
                         start_soc=40.0)
    base = now - timedelta(hours=3)
    for m in (0, 5, 10, 40):     # a 30-minute hole between the last two samples
        r.add_charging_sample(sid, _iso(base + timedelta(minutes=m)),
                              voltage_v=400.0, current_a=25.0, power_kw=10.0,
                              soc=40.0 + m / 2, battery_temp_c=15.0)
    r.close_session(sid, _iso(base + timedelta(minutes=40)), end_soc=60.0)
    empty = r.open_session(r.vehicle_id, _iso(now - timedelta(hours=1)), "DC")
    r.close_session(empty, _iso(now - timedelta(minutes=50)))
    return {"session": sid, "empty_session": empty}


@pytest.fixture()
def client(tmp_path):
    c, db, extra = _make_app(tmp_path, _populate)
    with c:
        c.db_path = db
        c.extra = extra
        yield c


@pytest.fixture()
def empty_client(tmp_path):
    c, _, _ = _make_app(tmp_path, None)
    with c:
        yield c


# ------------------------------------------------- 15. routes and APIs
@pytest.mark.parametrize("path", ["/", "/battery", "/range", "/charging",
                                  "/measurements", "/explore?key=pack_voltage",
                                  "/battery?days=1", "/range?days=7",
                                  "/battery?days=bogus"])
def test_chart_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:500]


@pytest.mark.parametrize("path", ["/", "/battery", "/range", "/charging"])
def test_chart_pages_render_without_any_data(empty_client, path):
    r = empty_client.get(path)
    assert r.status_code == 200, r.text[:500]


def test_battery_page_with_no_data_says_unavailable(empty_client):
    body = empty_client.get("/battery").text
    assert "Unavailable" in body
    assert "No individual cell readings" in body


def test_chart_series_api_payload(client):
    r = client.get("/api/chart/series", params={"keys": "soc_abs,pack_current",
                                                "days": 1})
    assert r.status_code == 200
    body = r.json()
    soc = body["series"]["soc_abs"]
    assert soc["unit"] == "%" and soc["axis"]["policy"] == cs.BOUNDED
    assert soc["verification"] in ("verified", "provisional", "unknown")
    assert soc["provenance_counts"].get("imported") == 1
    assert "imported" in soc["provenance"]
    assert body["series"]["pack_current"]["axis"]["policy"] == cs.SIGNED
    assert body["x_range"][0] <= body["x_range"][1]


@pytest.mark.parametrize("keys", ["", "a;drop table", ",".join(["k"] * 13)])
def test_chart_series_api_rejects_bad_keys(client, keys):
    assert client.get("/api/chart/series", params={"keys": keys}).status_code == 400


def test_legacy_series_payload_keys_still_present(client):
    body = client.get("/api/measurements/pack_voltage?days=1").json()
    for k in ("key", "name", "unit", "data_kind", "ts", "value", "raw_points",
              "returned_points", "downsampled", "method", "aliases"):
        assert k in body
    assert body["stats"]["n"] == 6


def test_unit_falls_back_to_stored_rows_when_catalogue_has_none(client):
    body = client.get("/api/measurements/lv_voltage_obd?days=1").json()
    assert body["unit"] == "V" and body["unit_source"] == "stored rows"


def test_cell_keys_are_numbered_like_the_catalogue(client):
    body = client.get("/api/measurements/cell_v_001?days=1").json()
    assert body["name"] == "Cell voltage #002"
    assert body["stats"]["n"] == 6


# --------------------------------------------------------- 1/F1 battery
def test_battery_page_keeps_soc_definitions_separate(client):
    body = client.get("/battery").text
    assert "State of charge (BMS)" in body and "State of charge (display)" in body
    assert "never merged" in body
    assert "Cell voltage spread per sweep" in body
    assert "cell #001" in body.lower() or "cell #002" in body.lower()


def test_provisional_resistance_dids_are_not_used_as_health(client):
    body = client.get("/battery").text
    for key in ("bat_internal_resistance_raw", "aux_battery_resistance_mohm"):
        assert key not in body
    from web.dashboard import BATTERY_PANELS, OVERVIEW_PANELS, RANGE_PANELS
    keys = {k for d in BATTERY_PANELS + OVERVIEW_PANELS + RANGE_PANELS
            for k in d["keys"]}
    assert not keys & {"bat_internal_resistance_raw", "aux_battery_resistance_mohm"}
    assert "ESTIMATED" in body


def test_overview_dual_axis_is_labelled(client):
    body = client.get("/").text
    assert "Left axis: %" in body and "Right axis: V" in body


# -------------------------------------------- 11. charging semantics
def test_charging_table_never_shows_missing_as_zero(client):
    body = client.get("/charging").text
    # The empty session has no samples, so no energy, power or temperature.
    assert "<td>0.00 kWh</td>" not in body
    assert "<td>0.0 kW</td>" not in body
    assert "<td>0.0 °C</td>" not in body
    assert "<td>—</td>" in body
    # ...while the session that has samples still shows its real values.
    assert "<td>10.0 kW</td>" in body


def test_charging_session_detail_shows_coverage_and_semantics(client):
    sid = client.extra["session"]
    body = client.get(f"/charging/{sid}").text
    assert "Session total, not a sample" in body
    assert "highest single instantaneous sample" in body
    assert "bridges" in body                 # the 30-minute hole is called out
    assert "Instantaneous power" in body and "Current (sampled)" in body
    assert client.get("/charging/9999").status_code == 404


# ----------------------------------------------- 12. range semantics
def test_range_page_keeps_estimate_distance_and_consumption_apart(client):
    body = client.get("/range").text
    assert "not distance travelled" in body
    assert "5 km" in body                    # odometer 111000 -> 111005
    assert "Not calculated" in body
    assert "No line is fitted" in body
    assert "6 pairs" in body                 # range readings 20 s after SoC


# --------------------------------------------- 16. read-only invariant
def test_browsing_chart_pages_never_writes_to_the_vehicle_log(client):
    for path in ("/", "/battery", "/range", "/charging",
                 f"/charging/{client.extra['session']}",
                 "/api/chart/series?keys=soc_abs"):
        client.get(path)
    con = sqlite3.connect(client.db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM tx_log").fetchone()[0] == 0
    finally:
        con.close()
