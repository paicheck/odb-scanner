"""A whole collector cycle must survive a clone-shaped adapter, not a friendly one.

The 29-bit modules went silent on the car while every test was green, because
the simulator accepted the hand-framed ATCAF0 line the field clone refuses (see
SimServer strict mode, simulator/vehicle.py). Unit tests of set_module did not
catch it either: they asserted what the code sent, not what the adapter accepts.

So this drives the real Collector through one cycle against a
`SimServer(strict=True)` -- the simulator now refuses the same three forms the
VEEPEAK clone does -- and checks that

  * every physical MEB module the simulator models returned at least one
    decoded value (a dead module is loud, not a silent NULL), and
  * nothing forbidden reached the wire: no ATCAF0, no hand-framed >7-byte line,
    and the adapter never once answered '?'.

`chg` is deliberately absent from the per-module check: diagnostic/ecus.py
gives it no tx29, so it is read functionally and is not a test of the module
addressing path.
"""
import socket
import string

import pytest

from database.repository import Repository
from diagnostic.ecus import ECUS
from simulator.vehicle import MEB_MODULES, SimServer
from tools.collect_acceptance import sim_config

_HEX = set(string.hexdigits)

# The ECUs the simulator models as physical MEB modules -- exactly the ones a
# broken module-addressing path makes silent.
MODULE_ECUS = sorted(
    key for key, spec in ECUS.items()
    if spec.tx29 is not None and spec.tx29 in MEB_MODULES
)


class _WireTap:
    """Records every command the transport sends and its response."""

    def __init__(self, inner):
        self.inner = inner
        self.commands: list[str] = []
        self.responses: list[tuple[str, list[str]]] = []

    def __call__(self, command: str) -> list[str]:
        self.commands.append(command)
        out = self.inner(command)
        self.responses.append((command, out))
        return out


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.slow
def test_every_modelled_meb_module_answers_a_clone_strict_adapter(tmp_path):
    port = _free_port()
    cfg = sim_config(port, str(tmp_path / "regress.db"))
    server = SimServer("127.0.0.1", port, strict=True)
    server.start()

    from collector import Collector

    repo = Repository(cfg.db_path)
    col = Collector(cfg, repo)
    tap = _WireTap(col.conn.t.send_command)
    col.conn.t.send_command = tap
    try:
        col.open_and_identify()
        col.discover_ecus()
        col.collect_once()

        rows = repo.conn.execute(
            "SELECT ecu, COUNT(*) AS n FROM measurements "
            "WHERE value IS NOT NULL GROUP BY ecu").fetchall()
        got = {row["ecu"]: row["n"] for row in rows}
        missing = [key for key in MODULE_ECUS if not got.get(key)]
        assert not missing, (
            f"the clone-strict simulator returned no data for physical MEB "
            f"module(s) {missing}; got {got}")
    finally:
        col.close()
        repo.close()
        server.stop()

    # --- the wire stayed clean ------------------------------------------------
    refusals = [cmd for cmd, out in tap.responses
                if any("?" in line for line in out)]
    assert not refusals, f"the clone refused command(s): {refusals}"
    assert "ATCAF0" not in tap.commands, (
        "the transport hand-framed a 29-bit module again (ATCAF0)")
    framed = [cmd for cmd in tap.commands
              if len(cmd) > 14 and all(ch in _HEX for ch in cmd)]
    assert not framed, f"hand-framed line(s) reached the wire: {framed}"
