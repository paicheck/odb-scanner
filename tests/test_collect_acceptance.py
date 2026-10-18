"""The live acceptance checker must be able to fail.

tools/collect_acceptance.py is the tool that decides whether a real run
"succeeded". A checker that cannot fail is a rubber stamp, and this project has
already had exactly one real collector run (12 Oct) where every
battery_measurements row was NULL and nothing said so. So the tool is exercised
here against the built-in simulator: first it must PASS a healthy run, then it
must FAIL when the HV-battery rows a broken run would leave behind are removed.

The simulator is the right harness for the first claim because it answers the
same MEB addresses the car does (BMS at 0x17FC007B -> 0x17FE007B, 102 live
cells), so a pass here is the checker agreeing with a known-good run rather
than with its own assumptions.
"""
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.repository import Repository, utcnow  # noqa: E402
from simulator.vehicle import SimServer  # noqa: E402
from tools.collect_acceptance import (  # noqa: E402
    Options,
    collect,
    evaluate,
    sim_config,
)


@pytest.fixture()
def sim_env(tmp_path):
    """A simulator on a free port plus a config pointed at a scratch database."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = SimServer("127.0.0.1", port)
    server.start()
    try:
        yield sim_config(port, str(tmp_path / "acceptance.db"))
    finally:
        server.stop()


def _essential_failures(results):
    return [r.name for r in results if r.essential and r.status == "FAIL"]


@pytest.mark.slow
def test_acceptance_passes_against_a_healthy_run(sim_env):
    state = collect(sim_env, cycles=1)
    with Repository(sim_env.db_path) as repo:
        results = evaluate(repo, state, Options())
    assert _essential_failures(results) == []
    # The checker must also have actually looked: a healthy simulator run has
    # every essential check green, not merely zero failures.
    assert sum(1 for r in results if r.status == "PASS") >= 6


@pytest.mark.slow
def test_acceptance_flags_the_observed_october_outcome(sim_env):
    """Reproduce the 12 Oct result: the run happened, the battery rows are empty."""
    state = collect(sim_env, cycles=1)
    with Repository(sim_env.db_path) as repo:
        repo.conn.execute(
            "DELETE FROM measurements WHERE key IN ('pack_voltage','soc_abs')")
        repo.conn.execute(
            "UPDATE battery_measurements SET pack_voltage_v=NULL, "
            "soc_abs_pct=NULL, soc_normal_pct=NULL")
        repo.conn.execute("DELETE FROM cell_voltages")
        repo.conn.commit()
        results = evaluate(repo, state, Options())
    failed = _essential_failures(results)
    assert any(n.startswith("pack_voltage") for n in failed), failed
    assert any(n.startswith("soc_abs") for n in failed), failed
    assert any(n.startswith("per-cell") for n in failed), failed
    assert any("battery_measurements row" in n for n in failed), failed


def test_unsupported_did_round_trip(repo):
    """mark_did_unsupported upserts per (ecu, did); keys are read back cleanly."""
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = utcnow()
    repo.mark_did_unsupported(vid, "energy", 0x2AB8, ts)
    repo.mark_did_unsupported(vid, "energy", 0x2AB8, ts)  # bump, not duplicate
    repo.mark_did_unsupported(vid, "bat_mgmt", 0x1234, ts)
    keys = repo.unsupported_did_keys(vid)
    assert ("energy", 0x2AB8) in keys
    assert ("bat_mgmt", 0x1234) in keys
    assert len(keys) == 2
    # a second vehicle is independent
    vid2 = repo.ensure_vehicle("WVWZZZE1ZMP099999")
    assert repo.unsupported_did_keys(vid2) == set()


def test_collector_marks_and_skips_unsupported_dids(sim_env):
    """A DID answered with NRC 0x31 is persisted and then skipped next poll."""
    from collector import Collector
    from decoders.registry import DIDSpec

    repo = Repository(sim_env.db_path)
    col = Collector(sim_env, repo)
    try:
        col.open_and_identify()
        vid = col.vehicle_id

        # The simulator's BMS implements a known DID set only; anything else
        # answers NRC 0x31 (requestOutOfRange) -- the exact signature the
        # filter keys on (the dominant failure on a real ID.3 whose Car
        # Scanner profile is generic MEB).
        spec = DIDSpec(key="bogus_probe", ecu_key="bat_mgmt", did=0x1234,
                       name="bogus", unit="")

        assert col.poll_did(spec, utcnow()) is None
        assert ("bat_mgmt", 0x1234) in col._unsupported
        assert ("bat_mgmt", 0x1234) in repo.unsupported_did_keys(vid)

        # The next poll skips without touching the wire (no new TX frame).
        before = repo.conn.execute(
            "SELECT COUNT(*) AS n FROM tx_log WHERE direction='TX'"
        ).fetchone()["n"]
        assert col.poll_did(spec, utcnow()) is None
        after = repo.conn.execute(
            "SELECT COUNT(*) AS n FROM tx_log WHERE direction='TX'"
        ).fetchone()["n"]
        assert after == before
    finally:
        col.close()
        repo.close()


def test_ever_succeeded_keys(repo):
    """ever_succeeded_keys lists UDS DIDs that have ever answered; failures and
    non-UDS rows are excluded."""
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = utcnow()
    repo.record_measurement(vid, ts, "k1", "energy", "UDS-0x22", "0x2AB2",
                            "Wh", "reported", "builtin", "experimental",
                            "04280400", 53200.0, None, True, None)
    repo.record_measurement(vid, ts, "k2", "bat_mgmt", "UDS-0x22", "0x028C",
                            "%", "reported", "builtin", "experimental",
                            "B3", 71.6, None, True, None)
    repo.record_measurement(vid, ts, "k3", "energy", "UDS-0x22", "0x2AF7",
                            "V", "reported", "builtin", "experimental",
                            "", None, None, False, "No/undecodable response")
    keys = repo.ever_succeeded_keys(vid)
    assert ("energy", 0x2AB2) in keys
    assert ("bat_mgmt", 0x028C) in keys
    assert ("energy", 0x2AF7) not in keys  # failed, so not "ever succeeded"
    # non-UDS rows (service != 'UDS-0x22') are excluded
    repo.record_measurement(vid, ts, "speed", "mot_elec", "01", "0D",
                            "km/h", "reported", "builtin", "documented",
                            "1E", 30.0, None, True, None)
    assert ("mot_elec", 0x0D) not in repo.ever_succeeded_keys(vid)


def _silent_collector(tmp_path, threshold=2):
    """A Collector over a scratch DB whose read_did returns NO DATA for every
    DID (use _no_data_except to let some DIDs answer)."""
    from collector import Collector
    from config import Config
    from diagnostic.interface import CommunicationError

    repo = Repository(str(tmp_path / "c.db"))
    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0,
                                "silent_retire_threshold": threshold},
                  "database": {"path": str(tmp_path / "c.db")}})
    col = Collector(cfg, repo)
    col.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053")

    def boom(ecu, did):
        raise CommunicationError(f"No/undecodable response to DID 0x{did:04X}")
    col.conn.read_did = boom
    return col, repo


def _no_data_except(col, *answer_dids):
    """Answer `answer_dids` with bytes; everything else returns NO DATA."""
    from diagnostic.interface import CommunicationError

    answering = set(answer_dids)

    def read(ecu, did):
        if did in answering:
            return b"\x00\x2B"
        raise CommunicationError(f"No/undecodable response to DID 0x{did:04X}")
    col.conn.read_did = read


def _answering_spec(col):
    """A real bat_mgmt DID the collector knows, preferably one with no decoder
    so the test does not depend on a payload of the right length."""
    specs = [s for s in col.registry.all()
             if s.ecu_key == "bat_mgmt" and s.did is not None]
    return next((s for s in specs if s.decode is None), specs[0])


def _rotate(col, specs, ts):
    """One retirement phase: poll `specs`, then let the collector judge them."""
    col._begin_silence_phase()
    out = [col.poll_did(spec, ts) for spec in specs]
    col._end_silence_phase(ts)
    return out


def test_never_answering_module_does_not_retire_its_dids(tmp_path):
    """Regression: a module that answered nothing at all is unreachable, not
    unsupported, so its DIDs must not be retired.

    On 2026-10-17 the adapter stopped reaching every 29-bit module for a whole
    cycle -- pack_voltage, soc_abs and pack_current all returned NO DATA while
    the 11-bit energy/climate/GPS modules answered normally. The old
    unconditional rule retired 72 DIDs in that one pass, odometer, VIN and gear
    among them, and every one of them answered again when re-read.
    """
    from decoders.registry import DIDSpec

    col, repo = _silent_collector(tmp_path, threshold=2)
    try:
        spec = DIDSpec(key="bogus", ecu_key="bat_mgmt", did=0x1234,
                       name="b", unit="")
        for _ in range(5):
            assert _rotate(col, [spec], utcnow()) == [None]
        assert ("bat_mgmt", 0x1234) not in col._unsupported
        assert repo.unsupported_did_keys(col.vehicle_id) == set()
    finally:
        repo.close()


def test_silent_did_is_retired_when_its_module_answers(tmp_path):
    """The positive case the guard must not break: a module that answered one
    DID has been reached, so a never-succeeded DID that stays silent really is
    unsupported and is retired at the threshold."""
    from decoders.registry import DIDSpec

    col, repo = _silent_collector(tmp_path, threshold=2)
    try:
        answering = _answering_spec(col)
        _no_data_except(col, answering.did)
        silent = DIDSpec(key="bogus", ecu_key="bat_mgmt", did=0x1234,
                         name="b", unit="")
        _rotate(col, [answering, silent], utcnow())
        assert ("bat_mgmt", 0x1234) not in col._unsupported     # 1 < threshold
        _rotate(col, [answering, silent], utcnow())
        assert ("bat_mgmt", 0x1234) in col._unsupported         # 2 == threshold
        assert ("bat_mgmt", 0x1234) in repo.unsupported_did_keys(col.vehicle_id)
    finally:
        repo.close()


def test_silent_did_never_retired_if_ever_succeeded(tmp_path):
    """A DID that has ever answered is never retired for being silent, even when
    its module is answering so the silence would otherwise count."""
    from decoders.registry import DIDSpec

    col, repo = _silent_collector(tmp_path, threshold=2)
    try:
        answering = _answering_spec(col)
        _no_data_except(col, answering.did)
        col._ever_succeeded.add(("bat_mgmt", 0x1234))
        spec = DIDSpec(key="bogus", ecu_key="bat_mgmt", did=0x1234,
                       name="b", unit="")
        for _ in range(5):
            assert _rotate(col, [answering, spec], utcnow())[1] is None
        assert ("bat_mgmt", 0x1234) not in col._unsupported
    finally:
        repo.close()
