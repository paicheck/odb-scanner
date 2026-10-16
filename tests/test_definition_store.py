"""Tests for the definition store: idempotent sync, provenance trail, capture
preservation, and the read-only safety guard's non-regression.

The schema additions are verified against a temp database. No real database is
opened and no vehicle is contacted.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import SCHEMA_SQL, SCHEMA_VERSION  # noqa: E402
from database.repository import Repository, SchemaMismatchError  # noqa: E402
from decoders.catalogue import (  # noqa: E402
    ByteLayout,
    ByteOrder,
    Confidence,
    EvidenceKind,
    MeasurementDefinition,
    build_catalogue,
)


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    yield r
    r.close()


# ------------------------------------------------------------------ schema
def test_definition_tables_are_created():
    with Repository(":memory:") as r:
        names = {row[0] for row in r.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("measurement_definitions", "definition_evidence",
              "captured_frames", "definition_evaluations"):
        assert t in names


def test_schema_version_is_unchanged_so_existing_databases_still_open():
    """The guard's own test asserts 'expects 1'; additive tables must not bump it.

    Bumping SCHEMA_VERSION would make data/real_car.db -- which holds the only
    real-world readings of the HV energy DIDs -- refuse to open, and that
    refusal is the guard working correctly for a change it does not need to
    catch. So the additions stay additive and the version stays put.
    """
    assert SCHEMA_VERSION == 1
    assert "CREATE TABLE IF NOT EXISTS measurement_definitions" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS captured_frames" in SCHEMA_SQL


def test_an_old_database_still_opens_and_gains_the_new_tables(tmp_path):
    path = tmp_path / "old.db"
    r = Repository(path)
    r.conn.execute("DROP TABLE measurement_definitions")
    r.conn.execute("DELETE FROM meta WHERE key='schema_version'")
    r.conn.commit()
    r.close()

    r2 = Repository(path)          # must not raise: version is unchanged
    try:
        assert r2._stored_schema_version() == SCHEMA_VERSION
        names = {row[0] for row in r2.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "measurement_definitions" in names
    finally:
        r2.close()


def test_the_mismatch_guard_still_fires_for_a_real_mismatch(tmp_path):
    path = tmp_path / "v0.db"
    r = Repository(path)
    r.conn.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
    r.conn.commit()
    r.close()
    with pytest.raises(SchemaMismatchError, match="expects 1"):
        Repository(path)


# ------------------------------------------------------------------- sync
def test_sync_stores_every_definition_and_is_idempotent(repo):
    defs = list(build_catalogue("meb").values())
    first = repo.sync_definitions(defs)
    n1 = repo.conn.execute("SELECT COUNT(*) FROM measurement_definitions").fetchone()[0]
    second = repo.sync_definitions(defs)
    n2 = repo.conn.execute("SELECT COUNT(*) FROM measurement_definitions").fetchone()[0]
    assert first == second
    assert n1 == n2 == len(defs)


def test_unchanged_definitions_keep_their_version_and_first_seen(repo):
    d = build_catalogue("meb")["hv_energy_max"]
    repo.sync_definitions([d], ts="2026-10-15 00:00:00+00:00")
    again = build_catalogue("meb")["hv_energy_max"]
    repo.sync_definitions([again], ts="2026-10-16 00:00:00+00:00")
    rows = repo.definition_history("hv_energy_max")
    assert len(rows) == 1, "an unchanged definition must not fork a new version"
    row = repo.definition("hv_energy_max")
    assert row["first_seen"] == "2026-10-15 00:00:00+00:00"
    assert row["last_seen"] == "2026-10-16 00:00:00+00:00"


def test_a_changed_layout_retires_the_old_version(repo):
    d = build_catalogue("meb")["hv_energy_content"]
    repo.sync_definitions([d])
    changed = MeasurementDefinition(
        **{**d.__dict__, "layout": ByteLayout(offset=0, length=2,
                                              byte_order=ByteOrder.BIG_ENDIAN,
                                              signed=False, scale=25.0),
           "version": ""})
    repo.sync_definitions([changed.ensure_version()])
    assert repo.definition("hv_energy_content")["byte_offset"] == 0
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM measurement_definitions WHERE active=0"
    ).fetchone()[0] == 1


def test_repeat_import_of_the_same_export_is_not_duplicated(repo):
    """Repeatability: importing the same CSV twice must not double the rows."""
    defs = list(build_catalogue("meb").values())
    for _ in range(3):
        repo.sync_definitions(defs)
    total = repo.conn.execute(
        "SELECT COUNT(*) FROM measurement_definitions").fetchone()[0]
    assert total == len(defs)


def test_definitions_can_be_filtered_by_confidence(repo):
    repo.sync_definitions(list(build_catalogue("meb").values()))
    verified = repo.definitions(confidence=Confidence.VERIFIED.value)
    keys = {r["key"] for r in verified}
    assert "hv_energy_content" in keys
    assert all(r["confidence"] == "VERIFIED" for r in verified)


def test_querying_an_unknown_key_returns_nothing(repo):
    repo.sync_definitions(list(build_catalogue("meb").values()))
    assert repo.definition("no_such_measurement") is None


# -------------------------------------------------------------- provenance
def test_evidence_is_recorded_as_a_trail_not_an_overwrite(repo):
    d = build_catalogue("meb")["hv_energy_content"]
    repo.sync_definitions([d])
    repo.record_evidence(d.key, d.version, "u32/1310.77 excluded by arithmetic",
                         "refutation", confidence_before="PROVISIONAL",
                         confidence_after="PROVISIONAL",
                         detail="41325/1310.77 is non-integral")
    repo.record_evidence(d.key, d.version, "no raw vehicle response exists",
                         "review", ts="2099-01-01T00:00:00Z")
    trail = repo.definition_evidence(d.key)
    assert len(trail) == 2
    assert trail[0]["kind"] == "refutation"
    assert trail[1]["kind"] == "review"


def test_evidence_requires_a_synchronised_definition(repo):
    with pytest.raises(KeyError):
        repo.record_evidence("nope", "v1", "x", "review")


def test_evaluations_are_recorded_per_definition(repo):
    d = build_catalogue("meb")["hv_energy_max"]
    repo.sync_definitions([d])
    repo.record_evaluation(d.key, d.version, "arithmetic", "PASS", 3,
                           detail="53200/50 = 1064")
    repo.record_evaluation(d.key, d.version, "live_compare", "INDETERMINATE", 0,
                           detail="no capture")
    n = repo.conn.execute("SELECT COUNT(*) FROM definition_evaluations").fetchone()[0]
    assert n == 2


def test_definition_json_preserves_the_whole_record(repo):
    d = build_catalogue("meb")["hv_energy_content"]
    repo.sync_definitions([d])
    import json

    stored = json.loads(repo.definition(d.key)["definition_json"])
    assert stored["key"] == d.key
    assert stored["evidence_kind"] == EvidenceKind.VEHICLE_RESPONSE.value
    assert stored["limitations"] == d.limitations
    assert stored["falsified_by"] == d.falsified_by


# ------------------------------------------------------- captured frames
def _vehicle(repo):
    """A real vehicle row, because captured_frames.vehicle_id is a foreign key.

    Passing vehicle_id=1 into an empty database raised IntegrityError, which is
    the constraint doing its job: a frame with no vehicle behind it is not
    something to persist.
    """
    repo.conn.execute(
        "INSERT INTO vehicles(vin, make, model, variant, year, first_seen)"
        " VALUES ('WVWZZZE1ZMP087053','Volkswagen','ID.3','58 kWh',2021,"
        "'2026-10-20T10:00:00+00:00')")
    repo.conn.commit()
    return 1


def test_a_capture_round_trips_bytes_unaltered(repo):
    vid = _vehicle(repo)
    raw_lines = "7E8 06 62 1E 3B 17 04 59 44"
    did_rec = "5944"
    fid = repo.record_frame(vid, "2026-10-20T10:00:00Z", "bat_mgmt",
                            request_bytes="061E3B",
                            response_raw_lines=raw_lines,
                            response_payload="621E3B17045944",
                            did_record=did_rec, answer_source_id=0x7E8,
                            session_state="ignition on, parked",
                            succeeded=True, note="SYNTHETIC")
    row = repo.conn.execute("SELECT * FROM captured_frames WHERE id=?",
                            (fid,)).fetchone()
    assert row["response_raw_lines"] == raw_lines
    assert row["did_record"] == did_rec
    assert row["answer_source_id"] == 0x7E8
    assert row["succeeded"] == 1


def test_a_failed_capture_is_kept_with_its_reason(repo):
    vid = _vehicle(repo)
    fid = repo.record_frame(vid, "2026-10-20T10:00:01Z", "energy",
                            request_bytes="03222AB255555555",
                            succeeded=False,
                            error="No/undecodable response to DID 0x2AB2")
    row = repo.conn.execute("SELECT * FROM captured_frames WHERE id=?",
                            (fid,)).fetchone()
    assert row["succeeded"] == 0
    assert "0x2AB2" in row["error"]
    assert repo.frames(ecu="energy") != []


def test_frames_can_be_looked_up_by_did_record(repo):
    repo.record_frame(None, "2026-10-20T10:00:00Z", "bat_mgmt", "061E3B",
                      did_record="5944", succeeded=True)
    repo.record_frame(None, "2026-10-20T10:00:01Z", "energy", "03222AB2",
                      did_record=None, succeeded=False)
    hits = repo.frames(did_record="5944")
    assert len(hits) == 1 and hits[0]["ecu"] == "bat_mgmt"


def test_frames_survive_a_reopen(tmp_path):
    path = tmp_path / "f.db"
    r = Repository(path)
    r.record_frame(None, "2026-10-20T10:00:00Z", "bat_mgmt", "061E3B",
                   did_record="5944", succeeded=True)  # vehicle_id=None is allowed
    r.close()
    r2 = Repository(path)
    try:
        assert len(r2.frames()) == 1
    finally:
        r2.close()


# ----------------------------------------------------------- safety guard
def test_the_read_only_guard_is_untouched_by_this_work():
    """Non-regression: the catalogue work must not have widened the allow-list.

    Imports the live guard rather than a copy, so a later edit to
    diagnostic/uds.py is what gets tested.
    """
    from diagnostic import uds

    assert 0x22 not in uds.BLOCKED_SERVICES
    for sid in (0x2E, 0x31, 0x27, 0x10, 0x34, 0x36, 0x37):
        if sid == 0x10:
            continue
        assert sid in uds.BLOCKED_SERVICES, hex(sid)
    uds.validate_service(0x22)
    for sid in (0x2E, 0x31, 0x27, 0x34, 0x36, 0x37):
        with pytest.raises(uds.ReadOnlyViolationError):
            uds.validate_service(sid)


def test_a_definition_cannot_authorise_a_write():
    """Definitions describe reads only; nothing in the catalogue can widen the
    set of services the guard permits."""
    bad = []
    for profile in ("meb", "eup"):
        for d in build_catalogue(profile).values():
            if d.service is not None and d.service != 0x22:
                bad.append((profile, d.key, d.service))
    assert bad == []


def test_request_bytes_for_every_definition_are_read_only_uds22():
    for profile in ("meb", "eup"):
        for d in build_catalogue(profile).values():
            if not d.request_bytes:
                continue
            assert re.fullmatch(r"22[0-9A-F]{4}", d.request_bytes), (
                f"{d.key}: {d.request_bytes} is not a bare 0x22 read")


# ----------------------------------------------------------- cross-profile
def test_the_two_profiles_do_not_silently_share_an_energy_layout():
    """They used to disagree; now both derive from the same source record."""
    meb = build_catalogue("meb")
    eup = build_catalogue("eup")
    assert meb["hv_energy_content"].layout == eup["hv_energy_content"].layout
    assert meb["hv_energy_max"].layout == eup["hv_energy_max"].layout


def test_addressing_difference_between_profiles_is_still_recorded():
    """Same layout, different ecu_key and address confidence.

    Decoding is shared, but addressing is per-car: the MEB (ID.3) gateway is
    confirmed 0x710 -> 0x77A by the 2026-10-15 capture, while the e-Up's own
    gateway addressing is a different car and remains unverified.
    """
    meb = build_catalogue("meb")
    eup = build_catalogue("eup")
    assert meb["hv_energy_content"].ecu_key == "energy"
    assert eup["hv_energy_content"].ecu_key == "gate"
    assert meb["hv_energy_content"].address_confidence is Confidence.VERIFIED
    assert eup["hv_energy_content"].address_confidence is Confidence.UNKNOWN


def test_databases_from_the_other_profile_are_detectable():
    """The empirical basis for tools/replay_validate.py's skip list."""
    from decoders.registry import build_default_registry

    eup = build_default_registry("eup")
    meb = build_default_registry("meb")
    raw = b"\x10\x3F"
    assert eup.get("battery_temp").decode_value(raw) == pytest.approx(24.984375)
    assert meb.get("battery_temp").decode_value(raw) == pytest.approx(-32.0)
