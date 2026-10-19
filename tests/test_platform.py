"""Tests for the vehicle-health investigation platform (Stages B–E).

Every fixture here is SYNTHETIC and lives in a throwaway database. None of it
describes the real car; the real vehicle is read only by the tools, never by
this test module. The point of these tests is the *logic*: quality gating,
freshness classification, downsampling, robust baselines, staged investigation
and decoder validation.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def repo(tmp_path):
    from database.repository import Repository
    r = Repository(tmp_path / "platform.db")
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="Test", model="Fixture")
    r.vehicle_id = vid
    yield r
    r.close()


def _now_iso(offset_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat(
        timespec="seconds")


def _record(repo, key, ts, value, *, success=True, raw="AB", ecu="bat_mgmt",
            provenance="reported", error=None):
    return repo.record_measurement(
        repo.vehicle_id, ts, key, ecu, "UDS-0x22", "0xF1A1", "V", provenance,
        "identity", "experimentally determined", raw, value=value,
        success=success, error=error)


# ---------------------------------------------------------------- quality
def test_known_bad_keys_are_excluded_from_scoring():
    from decoders.quality import quality_for, excluded_keys, Verification

    assert "dc_link_voltage" in excluded_keys("meb")
    assert "ecu_pcb_temperature_1" in excluded_keys("meb")
    q = quality_for("meb", "dc_link_voltage")
    assert q.verification is Verification.INVALID
    assert q.use_in_scoring is False
    assert "1738" in q.known_issue or "435" in q.known_issue


def test_healthy_keys_remain_usable():
    from decoders.quality import quality_for

    q = quality_for("meb", "pack_voltage")
    assert q.use_in_scoring is True
    assert q.plausible_min == 200.0 and q.plausible_max == 500.0
    # 436 V is inside the range; 1738 V is not.
    assert q.check(436.0)[0].value == "ok"
    assert q.check(1738.0)[0].value == "implausible"


def test_unknown_key_is_not_assumed_safe():
    from decoders.quality import quality_for

    q = quality_for("meb", "no_such_key_xyz")
    assert q.use_in_scoring is False
    assert q.verification.value == "unknown"


def test_data_kinds_are_classified():
    from decoders.quality import quality_for

    assert quality_for("meb", "gear").data_kind.value == "text"
    assert quality_for("meb", "energy_counters").data_kind.value == "json"
    assert quality_for("meb", "cell_delta_mv").data_kind.value == "derived"


# -------------------------------------------------------------- freshness
def test_freshness_states(repo):
    from analysis import freshness

    _record(repo, "soc_abs", _now_iso(-10), 50.0)
    assert freshness.observe_key(repo, repo.vehicle_id, "soc_abs").state \
        is freshness.ObservationState.FRESH

    # A separate key read once, long ago, is genuinely stale.
    _record(repo, "pack_voltage", _now_iso(-4000), 400.0)
    obs = freshness.observe_key(repo, repo.vehicle_id, "pack_voltage")
    assert obs.state is freshness.ObservationState.STALE


def test_missing_and_collector_failure(repo):
    from analysis import freshness

    assert freshness.observe_key(repo, repo.vehicle_id, "soc_abs").state \
        is freshness.ObservationState.MISSING
    _record(repo, "soc_abs", _now_iso(-5), None, success=False,
            error="No response")
    obs = freshness.observe_key(repo, repo.vehicle_id, "soc_abs")
    assert obs.state is freshness.ObservationState.COLLECTOR_FAILURE


def test_unparseable_timestamp_is_not_fresh():
    from analysis.freshness import ObservationState, _classify_rows

    rows = [{"ts": "not-a-date", "success": 1, "value": 1.0}]
    obs = _classify_rows(rows, "k", datetime.now(timezone.utc), 900.0)
    assert obs.state is ObservationState.UNKNOWN
    assert obs.fresh is False


def test_repeated_run_is_labelled_not_fresh_and_carried_forward_is_supported():
    from analysis.freshness import ObservationState, _classify_rows

    now = datetime.now(timezone.utc)
    rows = [{"ts": _iso(now, -i * 200), "success": 1, "value": 42.0,
             "raw_response": "AB"}
            for i in range(10)]
    obs = _classify_rows(rows, "k", now, 900.0)
    # 10 identical reads spanning 1800 s: same value, same bytes -> repeated.
    assert obs.state is ObservationState.REPEATED
    assert obs.fresh is True   # repeated is a real, current observation


def _iso(base, offset_s):
    return (base + timedelta(seconds=offset_s)).isoformat(timespec="seconds")


def test_imported_value_is_never_live(repo):
    from analysis import freshness

    _record(repo, "soc_abs", _now_iso(-5), 50.0, provenance="imported: Car Scanner CSV")
    obs = freshness.observe_key(repo, repo.vehicle_id, "soc_abs")
    assert obs.state is freshness.ObservationState.STALE
    assert obs.fresh is False


def test_module_silent_vs_idle(repo):
    from analysis import freshness

    # Two modules: one answered an hour ago, one ten hours ago, while the car
    # answered an hour ago. The stale one is silent, not merely idle.
    _record(repo, "soc_abs", _now_iso(-3600), 50.0, ecu="bat_mgmt")
    _record(repo, "obc_dc_current", _now_iso(-10 * 3600), 3.0, ecu="chg")
    statuses = {m.ecu: m for m in freshness.module_status(repo, repo.vehicle_id)}
    assert statuses["bat_mgmt"].status == "active"
    assert statuses["chg"].status == "silent"

    # If the WHOLE car is quiet, nothing is singled out as silent.
    repo.conn.execute("DELETE FROM measurements")
    _record(repo, "soc_abs", _now_iso(-10 * 3600), 50.0, ecu="bat_mgmt")
    _record(repo, "obc_dc_current", _now_iso(-10 * 3600), 3.0, ecu="chg")
    statuses = {m.ecu: m for m in freshness.module_status(repo, repo.vehicle_id)}
    assert statuses["bat_mgmt"].status == "idle"
    assert statuses["chg"].status == "idle"


# ------------------------------------------------------------------ series
def test_downsample_preserves_endpoints_and_bounds_length():
    from analysis import series

    pts = [(float(i), float(i * i)) for i in range(1000)]
    out = series.downsample(pts, 100, "lttb")
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert len(out) <= 100
    out2 = series.downsample(pts, 100, "min_max")
    assert out2[0] == pts[0] and out2[-1] == pts[-1]
    assert len(out2) <= 102


def test_min_max_keeps_a_spike_that_lttb_might_drop():
    from analysis import series

    pts = [(float(i), 1.0) for i in range(200)]
    pts[100] = (100.0, 999.0)
    out = series.min_max(pts, 20)
    assert any(y == 999.0 for _, y in out)


def test_numeric_payload_skips_text_values():
    from analysis import series

    ts = [_now_iso(i) for i in range(5)]
    values = [1.0, "P", 3.0, None, 5.0]
    payload = series.numeric_payload(ts, values, max_points=100)
    assert len(payload["value"]) == 3
    assert payload["raw_points"] == 3


def test_transitions_keep_only_changes():
    from analysis import series

    ts = [_now_iso(i) for i in range(6)]
    values = ["P", "P", "D", "D", "D", "R"]
    out = series.transitions(ts, values)
    assert out["value"] == ["P", "D", "R"]
    assert out["raw_points"] == 6


# ---------------------------------------------------------------- baseline
def test_robust_stats_hand_calculated():
    from analysis import baseline

    b = baseline.robust_stats([1, 2, 3, 4, 5])
    assert b.median == 3.0
    assert b.mad == 1.0
    assert round(b.sigma, 4) == round(1.4826, 4)
    assert round(b.robust_z(5), 3) == round(2 / 1.4826, 3)


def test_segments_split_on_a_large_gap():
    from analysis import baseline

    base = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
    ts = [_iso(base, 0), _iso(base, 60), _iso(base, 120),
          _iso(base, 120 + 7200), _iso(base, 120 + 7260)]
    spans = baseline.segments(ts, max_gap_s=3600)
    assert spans == [(0, 3), (3, 5)]


def test_detect_outliers_finds_a_spike_and_reports_sample_size():
    from analysis import baseline

    base = datetime(2026, 10, 12, tzinfo=timezone.utc)
    ts = [_iso(base, i * 60) for i in range(20)]
    values = [10.0] * 20
    values[10] = 90.0
    whole, findings = baseline.detect_outliers(ts, values)
    assert any(f.value == 90.0 for f in findings)
    assert findings[0].baseline["n"] == 20


def test_detect_outliers_refuses_on_too_few_samples():
    from analysis import baseline

    base = datetime(2026, 10, 12, tzinfo=timezone.utc)
    ts = [_iso(base, i * 60) for i in range(4)]
    _, findings = baseline.detect_outliers(ts, [1.0, 2.0, 99.0, 3.0])
    assert findings == []


def test_anomaly_scan_excludes_known_bad_keys(repo):
    from analysis import anomaly

    for i in range(20):
        _record(repo, "dc_link_voltage", _now_iso(-i * 60), 1738.0 + i)
    findings = anomaly.scan_robust(repo, repo.vehicle_id, "dc_link_voltage",
                                   "dc link", 30, profile="meb")
    assert findings == []
    assert repo.conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0


def test_robust_scan_ignores_text_keys_and_rows_without_crashing(repo):
    from analysis import anomaly

    for i in range(12):
        _record(repo, "gear", _now_iso(-i * 60), "P" if i % 2 else "D")
    assert anomaly.scan_robust(repo, repo.vehicle_id, "gear", "gear", 30,
                               profile="meb") == []
    # A single non-numeric row inside an otherwise numeric series must be
    # dropped, not fed to float().
    for i in range(10):
        _record(repo, "soc_abs", _now_iso(-i * 60), 50.0 + i)
    _record(repo, "soc_abs", _now_iso(0), "not-a-number")
    findings = anomaly.scan_robust(repo, repo.vehicle_id, "soc_abs", "soc", 30,
                                   profile="meb", persist=False)
    assert all(not isinstance(f.value, str) for f in findings)


def test_scan_all_robust_dry_run_writes_nothing(repo):
    from analysis import anomaly

    for i in range(20):
        _record(repo, "soc_abs", _now_iso(-i * 60), 50.0 + i)
        _record(repo, "gear", _now_iso(-i * 60), "P")
    report = anomaly.scan_all_robust(
        repo, repo.vehicle_id,
        ["soc_abs", "gear", "dc_link_voltage", "made_up_key"],
        profile="meb", persist=False)
    assert report["not_numeric"] == ["gear"]
    assert "dc_link_voltage" in report["skipped"]
    assert report["unknown"] == ["made_up_key"]
    assert report["scanned"] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0] == 0


# --------------------------------------------------------------- discovery
def test_discovery_search_and_facets(repo):
    from analysis import discovery

    _record(repo, "soc_abs", _now_iso(-10), 50.0)
    _record(repo, "dc_link_voltage", _now_iso(-10), 1738.0)
    entries = discovery.build_entries(repo, repo.vehicle_id, "meb")
    fac = discovery.facets(entries)
    assert fac["scoring"]["excluded"] >= 1
    hits = discovery.search_entries(entries, query="soc")
    assert any(e.key == "soc_abs" for e in hits)
    excluded = discovery.search_entries(entries, scoring=False)
    assert all(not e.use_in_scoring for e in excluded)


# ---------------------------------------------------------------- timeline
def test_timeline_orders_and_hides_simulated(repo):
    from analysis import timeline

    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    repo.add_event(repo.vehicle_id, _iso(t0, 0), "wake", "Woken")
    repo.add_event(repo.vehicle_id, _iso(t0, 60), "adapter",
                   "Connected via elm327 (SIM327 v1.5 (odb_scanner simulator))")
    repo.add_event(repo.vehicle_id, _iso(t0, 120), "note", "Real note")
    events = timeline.build_timeline(repo, repo.vehicle_id)
    titles = [e.title for e in events]
    assert "Real note" in titles
    assert not any("SIM327" in t for t in titles)
    with_sim = timeline.build_timeline(repo, repo.vehicle_id,
                                       include_simulated=True)
    assert any("SIM327" in e.title for e in with_sim)
    assert [e.ts for e in events] == sorted(e.ts for e in events)


# ----------------------------------------------------------- investigation
def test_investigation_refuses_diagnosis_without_criteria(repo):
    from analysis import investigation as inv

    i = inv.open_investigation(repo, repo.vehicle_id, "t", "s",
                               anchor_key="soc_abs")   # no criteria
    inv.observe_key(repo, repo.vehicle_id, "soc_abs", inv_id=i.id)
    inv.add_hypothesis(repo, i.id, "something", would_falsify="x")
    inv.add_counter_evidence(repo, i.id, "nothing against")
    done = inv.finalize(repo, i.id)
    assert done.status == "insufficient-evidence"
    assert "NOT CONFIRMED" in done.conclusion


def test_investigation_resolves_only_with_criteria_and_evidence(repo):
    from analysis import investigation as inv

    _record(repo, "soc_abs", _now_iso(-10), 60.0)
    i = inv.open_investigation(repo, repo.vehicle_id, "t", "s",
                               anchor_key="soc_abs",
                               criteria="a fresh soc_abs observation plus two "
                                        "more evidence rows")
    inv.observe_key(repo, repo.vehicle_id, "soc_abs", inv_id=i.id)
    inv.add_hypothesis(repo, i.id, "sensor drift", would_falsify="stable value")
    inv.add_counter_evidence(repo, i.id, "no other module disagrees")
    done = inv.finalize(repo, i.id, criteria_confirmed=True)
    assert done.status == "resolved"
    assert "CONFIRMED" in done.conclusion


def test_finalize_cannot_resolve_without_the_operator_confirming_criteria(repo):
    """The structural gate counts rows; it cannot read the prose criteria.

    A bare Finalise click must therefore stay at insufficient-evidence even
    when every structural check passes.
    """
    from analysis import investigation as inv

    _record(repo, "soc_abs", _now_iso(-10), 60.0)
    i = inv.open_investigation(repo, repo.vehicle_id, "t", "s",
                               anchor_key="soc_abs",
                               criteria="a fresh soc_abs observation plus two "
                                        "more evidence rows")
    inv.observe_key(repo, repo.vehicle_id, "soc_abs", inv_id=i.id)
    inv.add_hypothesis(repo, i.id, "sensor drift", would_falsify="stable value")
    inv.add_counter_evidence(repo, i.id, "no other module disagrees")
    done = inv.finalize(repo, i.id)          # no criteria_confirmed
    assert done.status == "insufficient-evidence"
    assert "not confirmed as satisfied" in done.conclusion


def test_association_is_never_certain(repo):
    from analysis import investigation as inv

    ts = _now_iso(-100)
    repo.add_event(repo.vehicle_id, ts, "note", "something happened")
    i = inv.open_investigation(repo, repo.vehicle_id, "t", "s")
    ev = inv.associate_events(repo, repo.vehicle_id, ts, inv_id=i.id)
    assert ev and all(not e.certain for e in ev)
    assert any("not causation" in e.detail for e in ev)


def test_additive_migration_adds_certain_to_an_older_evidence_table(tmp_path):
    """CREATE TABLE IF NOT EXISTS cannot add a column to an existing table.

    The real data/odb_scanner.db was created before investigation_evidence
    gained `certain`, so the first evidence write failed with "no such column".
    The additive ALTER must repair such a database in place, without dropping
    rows.
    """
    import sqlite3

    from database.repository import Repository

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE investigation_evidence ("
        " id INTEGER PRIMARY KEY,"
        " investigation_id INTEGER NOT NULL,"
        " ts TEXT NOT NULL,"
        " stage TEXT NOT NULL,"
        " kind TEXT NOT NULL,"
        " ref TEXT,"
        " observation TEXT NOT NULL,"
        " detail TEXT);")
    con.execute("INSERT INTO investigation_evidence "
                "(investigation_id, ts, stage, kind, observation) "
                "VALUES (1, '2026-01-01T00:00:00+00:00', 'observation', "
                "'measurement', 'kept')")
    con.commit()
    con.close()

    with Repository(path) as repo:
        cols = {r[1] for r in repo.conn.execute(
            "PRAGMA table_info(investigation_evidence)")}
        assert "certain" in cols
        row = repo.conn.execute(
            "SELECT observation, certain FROM investigation_evidence").fetchone()
        assert row["observation"] == "kept"
        assert row["certain"] == 1        # default backfills existing rows


def test_known_invalid_observation_cannot_support_a_diagnosis(repo):
    from analysis import investigation as inv

    _record(repo, "dc_link_voltage", _now_iso(-10), 1738.0)
    i = inv.open_investigation(repo, repo.vehicle_id, "t", "s",
                               anchor_key="dc_link_voltage",
                               criteria="a fresh scoreable observation")
    inv.observe_key(repo, repo.vehicle_id, "dc_link_voltage", inv_id=i.id)
    inv.add_hypothesis(repo, i.id, "h", would_falsify="x")
    inv.add_counter_evidence(repo, i.id, "c")
    done = inv.finalize(repo, i.id)
    assert done.status == "insufficient-evidence"


# ------------------------------------------------------------- decoder lab
def test_decoder_lab_flags_known_invalid(repo):
    from analysis import decoder_lab

    _record(repo, "dc_link_voltage", _now_iso(-10), 1738.0)
    ev = decoder_lab.evaluate_key(repo, repo.vehicle_id, "dc_link_voltage", "meb")
    assert ev.verdict == "FAIL"
    assert ev.verification == "invalid"


def test_decoder_lab_pass_for_a_healthy_key(repo):
    from analysis import decoder_lab

    # pack_voltage uses a u16/4 layout on the meb profile; craft raw bytes that
    # decode to a plausible 400 V and store the decoded value.
    from decoders.registry import build_default_registry
    spec = build_default_registry("meb").get("pack_voltage")
    raw = bytes([0x06, 0x40])
    value = spec.decode_value(raw)
    assert value is not None and 200.0 <= value <= 500.0
    _record(repo, "pack_voltage", _now_iso(-10), value, raw=raw.hex().upper())
    ev = decoder_lab.evaluate_key(repo, repo.vehicle_id, "pack_voltage", "meb")
    assert ev.verdict in ("PASS", "INDETERMINATE"), ev.issues
    if ev.verdict == "PASS":
        assert ev.n_samples == 1


def test_decoder_lab_ignores_imported_csv_rows_as_evidence(repo):
    """An imported Car Scanner row stores the CSV line in raw_response, not UDS
    bytes. It is evidence about the car, but it must never validate *our*
    decoder, and its non-hex 'raw' must not be flagged as an undecodable
    vehicle response (which wrongly failed 40-odd cell keys)."""
    from analysis import decoder_lab

    _record(repo, "cell_v_003", _now_iso(-10), 3.996,
            provenance="imported: Car Scanner CSV",
            raw="73297.5765382;[8C.BMS] HV Battery cell voltage #004;3.996;V")
    ev = decoder_lab.evaluate_key(repo, repo.vehicle_id, "cell_v_003", "meb")
    assert ev.verdict == "INDETERMINATE"
    assert not any("not decodable" in i for i in ev.issues)


def test_decoder_lab_persist_writes_evaluations(repo):
    from analysis import decoder_lab

    _record(repo, "soc_abs", _now_iso(-10), 50.0)
    evals = [decoder_lab.evaluate_key(repo, repo.vehicle_id, "soc_abs", "meb")]
    n = decoder_lab.persist_evaluations(repo, "meb", evals)
    assert n == 1
    stored = repo.conn.execute(
        "SELECT COUNT(*) FROM definition_evaluations").fetchone()[0]
    assert stored == 1


# ---------------------------------------------------------------- schema
def test_new_tables_exist_and_version_is_unchanged():
    from database.models import SCHEMA_SQL, SCHEMA_VERSION
    from database.repository import Repository

    assert SCHEMA_VERSION == 1
    for t in ("baselines", "investigations", "investigation_evidence"):
        assert f"CREATE TABLE IF NOT EXISTS {t}" in SCHEMA_SQL
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with Repository(os.path.join(tmp, "s.db")) as r:
            names = {row[0] for row in r.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"baselines", "investigations", "investigation_evidence"} <= names


# ------------------------------------------------------------------- web
@pytest.fixture()
def dashboard(tmp_path):
    from config import Config
    from database.repository import Repository
    from web.dashboard import create_app

    db = tmp_path / "platform_web.db"
    cfg = Config({
        "database": {"path": str(db)},
        "vehicle": {"did_profile": "meb"},
        "web": {"host": "127.0.0.1"},
        "collector": {"max_value_age_s": 900, "slow_poll_interval": 60,
                      "poll_interval": 5},
        "analysis": {"trend_window_days": 30, "charging_window_days": 90},
    })
    r = Repository(str(db))
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    common = dict(ecu="bat_mgmt", service="22", pid="F1A1",
                  provenance="reported", decoding_method="identity",
                  doc_status="experimentally determined", raw_response="")
    for i in range(15):
        r.record_measurement(vid, f"2026-10-18T14:{i:02d}:00+00:00", "soc_abs",
                             value=50 + i, unit="%", **common)
    r.record_measurement(vid, "2026-10-18T14:00:00+00:00", "dc_link_voltage",
                         value=1738.0, unit="V", **common)
    r.close()

    app = create_app(cfg, bind_host="127.0.0.1")
    from fastapi.testclient import TestClient
    with TestClient(app, follow_redirects=True) as client:
        yield client


@pytest.mark.parametrize("path", ["/explore", "/status", "/quality",
                                  "/investigations", "/api/discover",
                                  "/api/timeline", "/api/investigations"])
def test_platform_pages_render(dashboard, path):
    r = dashboard.get(path)
    assert r.status_code == 200, r.text[:500]


def test_series_api_is_downsampled(dashboard):
    r = dashboard.get("/api/series?key=soc_abs&max_points=5")
    assert r.status_code == 200
    body = r.json()
    assert body["raw_points"] == 15
    assert body["returned_points"] <= 6
    assert body["downsampled"] is True


def test_export_series_csv_keeps_every_column_and_row(dashboard):
    r = dashboard.get("/api/export/series?key=soc_abs&format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    lines = [ln for ln in r.text.splitlines() if ln]
    assert lines[0].split(",")[0] == "ts"
    assert "raw_response" in lines[0]
    assert "success" in lines[0]
    assert len(lines) == 16  # header + 15 stored rows


def test_export_series_json_carries_observation_and_limitations(dashboard):
    r = dashboard.get("/api/export/series?key=soc_abs&format=json")
    body = r.json()
    assert body["key"] == "soc_abs"
    assert body["row_count"] == 15
    assert body["observation"]["state"]
    assert body["limitations"]
    assert body["rows"][0]["success"] == 1


def test_export_series_requires_a_key(dashboard):
    assert dashboard.get("/api/export/series").status_code == 400


def test_export_discovery_csv(dashboard):
    r = dashboard.get("/api/export/discovery?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "soc_abs" in r.text


def test_export_investigation_packet(dashboard):
    dashboard.post("/investigations/new",
                   data={"title": "T", "symptom": "s", "criteria": "c"})
    js = dashboard.get("/api/export/investigation/1?format=json")
    assert js.status_code == 200
    body = js.json()
    assert body["investigation"]["title"] == "T"
    assert body["limitations"]
    csvr = dashboard.get("/api/export/investigation/1?format=csv")
    assert csvr.status_code == 200
    assert csvr.text.splitlines()[0].startswith("stage,kind,observation")
    assert dashboard.get("/api/export/investigation/999").status_code == 404
