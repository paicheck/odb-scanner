"""tools/purge_synthetic_data.py must remove exactly the synthetic rows.

The purge is the one irreversible step in cleaning up a database that a
simulator and a demo seeder wrote into, so the tests below pin both halves of
the contract: everything older than the cutoff goes, and the three things that
must survive -- imported Car Scanner readings, the adapter log, and all real
readings -- stay. A rule that is merely *close* is not enough: a single
surviving simulator row is indistinguishable from a measurement.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.repository import Repository  # noqa: E402
from tools import purge_synthetic_data as purge  # noqa: E402

CUTOFF = "2026-10-05T00:00:00+00:00"
OLD = "2026-09-20T12:00:00+00:00"        # simulator era
NEW = "2026-10-06T12:00:00+00:00"        # real era
ORPHAN = "2026-10-07T12:00:00+00:00"     # after the cutoff, still fake
SESSION = "2026-10-10T12:00:00+00:00"    # a surviving session
SESSION_END = "2026-10-10T13:00:00+00:00"


def _measurement(repo, vid, ts, provenance, key="soc_abs"):
    repo.conn.execute(
        "INSERT INTO measurements(vehicle_id, ts, ecu, service, pid, key, "
        "value, unit, provenance, decoding_method, doc_status, success) "
        "VALUES (?,?,'bat_mgmt','UDS-0x22','0x028C',?,55.0,'%',?,'builtin',"
        "'experimentally determined',1)",
        (vid, ts, key, provenance))


def _event(repo, vid, ts, kind, description="x"):
    repo.conn.execute(
        "INSERT INTO diagnostic_events(vehicle_id, ts, kind, description) "
        "VALUES (?,?,?,?)", (vid, ts, kind, description))


def build(path) -> tuple[Repository, int]:
    """A database holding one of everything, old and new. Returns (repo, vid)."""
    repo = Repository(path)
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    repo.conn.execute("UPDATE vehicles SET first_seen = ? WHERE id = ?",
                      (OLD, vid))

    _measurement(repo, vid, OLD, "reported")
    _measurement(repo, vid, OLD, "imported: Car Scanner CSV", key="soc_normal")
    _measurement(repo, vid, NEW, "reported")
    _measurement(repo, vid, NEW, "simulated", key="pack_voltage")

    for ts in (OLD, NEW):
        repo.conn.execute(
            "INSERT INTO cell_voltages(vehicle_id, ts, cell_index, voltage_v) "
            "VALUES (?,?,1,3.9)", (vid, ts))
        repo.conn.execute(
            "INSERT INTO battery_measurements(vehicle_id, ts, soc_abs_pct) "
            "VALUES (?,?,50.0)", (vid, ts))
        repo.conn.execute(
            "INSERT INTO dtcs(vehicle_id, ecu, code, categories, first_seen, "
            "last_seen) VALUES (?,'chg_mgmt',?,'[]',?,?)",
            (vid, "U112300" if ts == OLD else "U010000", ts, ts))
        repo.conn.execute(
            "INSERT INTO anomalies(vehicle_id, ts, metric) VALUES (?,?,?)",
            (vid, ts, "cell_delta_mv"))
        repo.conn.execute(
            "INSERT INTO analysis_results(vehicle_id, ts, analysis_type, "
            "result_json) VALUES (?,?,'battery-cell-delta','{}')", (vid, ts))
        repo.conn.execute(
            "INSERT INTO tx_log(ts, direction, payload) VALUES (?,'TX','0902')",
            (ts,))

    # charging: one old session (synthetic) and one new one (real), each with a
    # sample and its boundary events.
    old_sid = repo.conn.execute(
        "INSERT INTO charging_sessions(vehicle_id, charge_type, started_at) "
        "VALUES (?,'AC',?)", (vid, OLD)).lastrowid
    new_sid = repo.conn.execute(
        "INSERT INTO charging_sessions(vehicle_id, charge_type, started_at, "
        "ended_at, status) VALUES (?,'AC',?,?,'completed')",
        (vid, SESSION, SESSION_END)).lastrowid
    for sid, ts in ((old_sid, OLD), (new_sid, SESSION)):
        repo.conn.execute(
            "INSERT INTO charging_samples(session_id, ts, voltage_v) "
            "VALUES (?,?,232.0)", (sid, ts))
    _event(repo, vid, OLD, "charge-start")
    _event(repo, vid, OLD, "charge-end")
    _event(repo, vid, SESSION, "charge-start")
    _event(repo, vid, SESSION_END, "charge-end")
    _event(repo, vid, ORPHAN, "charge-end")     # orphan: no session covers it

    # dtc_occurrences hang off the old dtc, which is itself going.
    dtc_id = repo.conn.execute(
        "SELECT id FROM dtcs WHERE first_seen = ?", (OLD,)).fetchone()["id"]
    repo.conn.execute(
        "INSERT INTO dtc_occurrences(dtc_id, ts) VALUES (?,?)", (dtc_id, OLD))

    _event(repo, vid, OLD, "note", "old note")
    _event(repo, vid, OLD, "adapter", "Connected via SIM327")
    _event(repo, vid, NEW, "note", "new note")
    repo.conn.commit()
    return repo, vid


def _count(repo, table) -> int:
    return repo.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def test_the_plan_counts_exactly_what_the_purge_removes(tmp_path):
    """A dry run that disagrees with the apply is worse than no dry run."""
    repo, _ = build(tmp_path / "t.db")
    try:
        todo = purge.plan(repo, CUTOFF)
        removed = purge.purge(repo, CUTOFF)
        assert todo == removed
    finally:
        repo.close()


def test_every_old_row_goes_and_every_new_one_stays(tmp_path):
    repo, _ = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF)
        # Old rows are gone...
        for table, column in (("cell_voltages", "ts"),
                              ("battery_measurements", "ts"),
                              ("anomalies", "ts"),
                              ("analysis_results", "ts")):
            old = repo.conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" < ?',
                (CUTOFF,)).fetchone()[0]
            assert old == 0, f"{table} kept a synthetic row"
        # ...and the new ones are untouched.
        assert _count(repo, "charging_sessions") == 1
        assert _count(repo, "charging_samples") == 1
        assert _count(repo, "dtcs") == 1
        assert _count(repo, "tx_log") == 1
    finally:
        repo.close()


def test_imported_readings_survive_however_old_they_are(tmp_path):
    """A Car Scanner export is a real reading of the car by another tool."""
    repo, _ = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF)
        kept = [row["provenance"] for row in repo.conn.execute(
            "SELECT provenance FROM measurements ORDER BY ts")]
        assert "imported: Car Scanner CSV" in kept
        assert "reported" in kept
        assert "simulated" not in kept, "a simulated row survived the purge"
        assert len(kept) == 2
    finally:
        repo.close()


def test_the_adapter_log_survives_as_the_evidence(tmp_path):
    """The 'Connected via SIM327' line is why the purge is defensible."""
    repo, _ = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF)
        kinds = sorted(
            row["kind"] for row in repo.conn.execute(
                "SELECT kind FROM diagnostic_events ORDER BY kind"))
        # The old note and the old session's boundaries are gone; the adapter
        # line is not; the new note and the real session's boundaries are kept.
        assert kinds == ["adapter", "charge-end", "charge-start", "note"]
        assert repo.conn.execute(
            "SELECT description FROM diagnostic_events WHERE kind='adapter'"
        ).fetchone()["description"].startswith("Connected via SIM327")
    finally:
        repo.close()


def test_a_charge_event_with_no_session_to_belong_to_is_dropped(tmp_path):
    """The simulator left sessions open, so their closing stamps outlive them
    and sit after the cutoff -- where a plain date rule would spare them."""
    repo, _ = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF)
        stamps = sorted(row["ts"] for row in repo.conn.execute(
            "SELECT ts FROM diagnostic_events WHERE kind LIKE 'charge-%'"))
        assert stamps == [SESSION, SESSION_END], stamps
        assert ORPHAN not in stamps
    finally:
        repo.close()


def test_first_seen_is_repointed_at_the_oldest_surviving_reading(tmp_path):
    """The vehicle row was created by the first simulator connection.

    "First seen" means the oldest surviving observation of the car, so imported
    Car Scanner evidence counts -- it is a real reading of the car, taken by
    another tool. What must not survive is a stamp older than any reading,
    which is all the simulator's first connection ever was.
    """
    repo, vid = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF)
        first = repo.conn.execute(
            "SELECT first_seen FROM vehicles WHERE id = ?", (vid,)).fetchone()
        oldest = repo.conn.execute(
            "SELECT MIN(ts) FROM measurements").fetchone()[0]
        assert oldest == OLD, "fixture changed: the imported row moved"
        assert first["first_seen"] == oldest
    finally:
        repo.close()


def test_keep_tx_log_leaves_the_safety_manifest_alone(tmp_path):
    """tx_log is normally kept forever, so the flag is the conservative path."""
    repo, _ = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF, keep_tx_log=True)
        assert _count(repo, "tx_log") == 2
    finally:
        repo.close()


def test_purging_a_clean_database_removes_nothing(tmp_path):
    repo, _ = build(tmp_path / "t.db")
    try:
        purge.purge(repo, CUTOFF)
        before = {t: _count(repo, t) for t, _, _ in purge.RULES}
        assert purge.plan(repo, CUTOFF) == {t: 0 for t in before}
        assert purge.purge(repo, CUTOFF) == {t: 0 for t in before}
        assert {t: _count(repo, t) for t, _, _ in purge.RULES} == before
    finally:
        repo.close()
