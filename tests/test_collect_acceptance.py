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

from database.repository import Repository  # noqa: E402
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
