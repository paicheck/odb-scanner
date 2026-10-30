"""A row the collector wrote while talking to the simulator must say so.

Separating the files (tests/test_synthetic_data_separation.py) stops the *usual*
way simulator data reaches the car's record. This is the backstop for when it
happens anyway -- a hand-run collector, an ODB_DB_PATH that did not apply, a
database restored from a backup taken before the split. The simulator decodes
through exactly the same code path as the car, so nothing about the value gives
it away: the only evidence is the adapter it answered through.

So the collector records `provenance='simulated'` for every row it writes in
that state, the dashboard reports those rows as simulated rather than live, and
the cloud uploader refuses to send them -- the cloud schema is keyed by VIN, so
an uploaded row would be filed as this car's own reading.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CAR_VIN = "WVWZZZE1ZMS000001"


# --- recognising the simulator ----------------------------------------------
def test_the_simulators_ati_string_is_recognised():
    from collector import is_simulator
    assert is_simulator("ATI SIM327 v1.5 (odb_scanner simulator)") is True
    assert is_simulator("odb_scanner simulator") is True


def test_a_real_adapters_ati_string_is_not():
    from collector import is_simulator
    assert is_simulator("ATI ELM327 v1.5") is False
    assert is_simulator("ATI OBDLink CX v2.0") is False
    assert is_simulator("") is False
    assert is_simulator(None) is False


def _tcp_config():
    """Default config, pointed at a TCP adapter (the collector is never
    connected here; only the provenance helper is exercised)."""
    import yaml
    from config import Config, DEFAULT_PATH
    with open(DEFAULT_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("adapter", {})["type"] = "elm327_tcp"
    return Config(data)


# --- labelling the rows ------------------------------------------------------
def test_a_simulated_run_relabels_every_provenance(repo):
    """'simulated' beats whatever the decoder claims about the row.

    A simulated value is decoded by exactly the same code path as a real one,
    so `reported` is the default it would otherwise carry -- and it would be a
    lie about where the number came from.
    """
    from collector import Collector
    from decoders.registry import Provenance

    col = Collector(_tcp_config(), repo)
    assert col.simulated is False, "a fresh collector must not assume simulated"
    assert col._prov(Provenance.REPORTED) == "reported"
    assert col._prov(Provenance.ESTIMATED) == "estimated"

    col.simulated = True
    for claimed in (Provenance.REPORTED, Provenance.CALCULATED,
                    Provenance.ESTIMATED):
        assert col._prov(claimed) == Provenance.SIMULATED.value


def test_the_simulated_provenance_is_a_distinct_label():
    """Nothing may treat it as one of the existing sources."""
    from analysis.provenance import SIMULATED_PROVENANCE
    from database.repository import IMPORTED_PREFIX
    from decoders.registry import Provenance

    assert Provenance.SIMULATED.value == "simulated"
    assert SIMULATED_PROVENANCE == "simulated"
    assert not SIMULATED_PROVENANCE.startswith(IMPORTED_PREFIX)


@pytest.mark.slow
def test_a_full_simulator_run_is_tagged_simulated(tmp_path):
    """End to end: connect to the built-in simulator and check the rows.

    The unit test above proves the helper; this one proves the collector
    actually routes its writes through it, which is the part that would break
    silently.
    """
    import socket

    from collector import Collector
    from database.repository import Repository
    from simulator.vehicle import SimServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = SimServer("127.0.0.1", port, strict=True, vin=CAR_VIN)
    server.start()
    repo = Repository(str(tmp_path / "sim.db"))
    try:
        config = _tcp_config()
        config._data["adapter"]["tcp_port"] = port
        config._data["collector"]["slow_poll_interval"] = 3600.0
        config._data.setdefault("simulation", {})["db_path"] = repo.path
        col = Collector(config, repo)
        try:
            col.open_and_identify()
            # The VIN is pinned to the car's on purpose: the tag must come from
            # the adapter, not from a VIN mismatch, or a simulator emulating the
            # car would slip through.
            assert col.vin == CAR_VIN
            assert col.simulated is True
            col.collect_once()
        finally:
            col.close()

        sources = [row["provenance"] for row in repo.conn.execute(
            "SELECT DISTINCT provenance FROM measurements")]
        assert sources == ["simulated"], sources
        assert repo.conn.execute(
            "SELECT COUNT(*) FROM measurements").fetchone()[0] > 0
    finally:
        repo.close()
        server.stop()


# --- reporting and shipping --------------------------------------------------
def test_the_sources_summary_counts_simulated_separately(repo):
    """On /sources a simulated row must not be counted as a current reading."""
    from datetime import datetime, timezone

    from analysis.provenance import key_states, summary

    vid = repo.ensure_vehicle(CAR_VIN)
    # Written "now": the live state is a function of the timestamp, so an old
    # row would read as stale and the assertion would pass for the wrong reason.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, provenance in (("soc_abs", "simulated"),
                            ("pack_voltage", "reported")):
        repo.record_measurement(
            vid, now, key, "bat_mgmt", "UDS-0x22",
            "0x028C", "V", provenance, "builtin", "experimentally determined",
            "", 1.0, None, True, None)
    repo.conn.commit()

    from decoders.registry import build_default_registry
    rows = key_states(repo, vid, build_default_registry("meb"))
    counts = summary(rows)
    assert counts["simulated"] == 1
    assert counts["live"] == 1


def test_the_cloud_uploader_never_sends_a_simulated_row(repo):
    """Nothing else filters these on the way out, and the ingest API keys rows
    by VIN -- so a simulated row would be indexed as this car's own data."""
    from cloud.client import pending_rows

    vid = repo.ensure_vehicle(CAR_VIN)
    for ts, provenance in (("2026-10-06T12:00:00+00:00", "reported"),
                           ("2026-10-06T12:01:00+00:00", "simulated"),
                           ("2026-10-06T12:02:00+00:00", "reported")):
        repo.record_measurement(
            vid, ts, "soc_abs", "bat_mgmt", "UDS-0x22", "0x028C", "%",
            provenance, "builtin", "experimentally determined", "", 55.0,
            None, True, None)
    repo.conn.commit()

    rows = pending_rows(repo, 0, 100)
    assert [row["provenance"] for row in rows] == ["reported", "reported"]


def test_a_simulator_run_into_another_database_is_refused(tmp_path):
    """Labelling is not separation: refuse before anything is written.

    `main.py collect` pointed at the simulator wrote a new vehicle (the demo
    VIN) into the car's database, and first_vehicle() then showed the
    simulated car on the dashboard; snapshots, sessions and DTCs carry no
    provenance column to tell them apart.
    """
    import socket

    import pytest

    from collector import Collector, SimulatorIntoCarDatabaseError
    from database.repository import Repository
    from simulator.vehicle import SimServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = SimServer("127.0.0.1", port, strict=True)
    server.start()
    repo = Repository(str(tmp_path / "car.db"))
    try:
        config = _tcp_config()
        config._data["adapter"]["tcp_port"] = port
        config._data.setdefault("simulation", {})["db_path"] = str(tmp_path / "sim.db")
        col = Collector(config, repo)
        with pytest.raises(SimulatorIntoCarDatabaseError):
            col.open_and_identify()
        for table in ("vehicles", "measurements", "diagnostic_events"):
            assert repo.conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        repo.close()
        server.stop()
