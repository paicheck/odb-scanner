"""Safety and behaviour tests for the read-only [19.Gate] scan.

The tool is meant to be pointed at live hardware, so these tests pin the
things that must not drift:
  * the DID allow-list is the profile's [19.Gate] set, never the quarantined
    DIDs (0x2A26/0x2A28/0x2A2F)
  * every request is UDS 0x22 (read-only), with the adapter's warm-up 0x0100
    the only other vehicle request
  * the registry decoders produce the expected values on the simulator, which
    implements the 11-bit gateway at 0x710 -> 0x77A
"""
import socket

import pytest


@pytest.fixture()
def sim_port():
    """A simulator on a free port, closed before cleanup."""
    from simulator.vehicle import SimServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = SimServer("127.0.0.1", port)
    server.start()
    try:
        yield port
    finally:
        server.stop()


def run_scan(port):
    from decoders.registry import build_default_registry
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.elm327 import Elm327Transport
    from tools.scan_gateway import scan

    transport = Elm327Transport(host="127.0.0.1", tcp_port=port, timeout=5.0)
    conn = DiagnosticConnection(transport)
    conn.open()
    try:
        return scan(conn, build_default_registry("meb"))
    finally:
        conn.close()


def by_did(results):
    return {int(entry["did"], 16): entry for entry in results}


def test_allow_list_is_the_profile_gateway_set():
    from tools.scan_gateway import ALLOWED_DIDS, GATEWAY_DIDS, QUARANTINED_DIDS

    assert {did for did, _ in GATEWAY_DIDS} == {
        0x2AB2,
        0x2AB4,
        0x2AB5,
        0x2AB6,
        0x2AB7,
        0x2AB8,
        0x2A53,
        0x2AF7,
        0x15D1,
    }
    assert not ALLOWED_DIDS & QUARANTINED_DIDS


def test_every_gateway_did_has_a_registry_decoder():
    from decoders.registry import build_default_registry
    from tools.scan_gateway import build_plan

    for item in build_plan(build_default_registry("meb")):
        assert item["request"].startswith("22")
        assert item["decoders"], f"no decoder for {item['did']}"


def test_only_refuses_dids_outside_the_allow_list():
    from tools.scan_gateway import parse_only

    with pytest.raises(SystemExit):
        parse_only("2A26")
    with pytest.raises(SystemExit):
        parse_only("1234")
    assert parse_only("2AB8, 2AF7") == {0x2AB8, 0x2AF7}
    assert parse_only("0x2AB8") == {0x2AB8}


@pytest.mark.slow
def test_scan_reads_the_gateway_on_the_simulator(sim_port):
    from tools.scan_gateway import GATEWAY_DIDS

    results = run_scan(sim_port)
    scanned = by_did(results)
    assert set(scanned) == {did for did, _ in GATEWAY_DIDS}

    # The simulator implements these three on 0x710. Each must be a real
    # positive answer with exactly the request the tool claims it sent.
    for did in (0x2AB2, 0x2AB8, 0x2AF7):
        entry = scanned[did]
        assert entry["error"] is None
        assert entry["record_hex"]
        assert entry["tx"] == [f"22{did:04X}"]
        assert entry["rx"]

    # The rest are not in the simulator's persona. A missing answer must stay
    # a missing answer -- never a fabricated value.
    for did in (0x2AB4, 0x2AB5, 0x2AB6, 0x2AB7, 0x2A53, 0x15D1):
        entry = scanned[did]
        assert entry["record_hex"] is None
        assert entry["decoded"] == []
        assert entry["error"] is not None


@pytest.mark.slow
def test_scan_decodes_the_simulator_gateway_values(sim_port):
    scanned = by_did(run_scan(sim_port))

    def value(did, key):
        for item in scanned[did]["decoded"]:
            if item["key"] == key:
                return item["value"]
        raise AssertionError(f"{key} not decoded for 0x{did:04X}")

    assert value(0x2AB2, "hv_energy_max") == 53200.0
    assert value(0x2AB8, "hv_energy_content") == 41200.0
    assert value(0x2AF7, "aux_12v_voltage") == pytest.approx(14.52, abs=0.001)


@pytest.mark.slow
def test_scan_sends_only_read_requests(sim_port, monkeypatch):
    from diagnostic.elm327 import Elm327Transport

    seen: list[str] = []
    original = Elm327Transport.send_command

    def recording(self, command):
        seen.append(command.upper())
        return original(self, command)

    monkeypatch.setattr(Elm327Transport, "send_command", recording)
    run_scan(sim_port)

    vehicle_requests = [cmd for cmd in seen if not cmd.startswith("AT")]
    assert vehicle_requests, "no vehicle request recorded"
    # 0100 is the transport's standard OBD-II warm-up in initialize(); every
    # other vehicle request must be a 0x22 read, and all of them must be on
    # the gateway allow-list.
    assert all(cmd == "0100" or cmd.startswith("22") for cmd in vehicle_requests)
    reads = [cmd for cmd in vehicle_requests if cmd.startswith("22")]
    assert len(reads) == 9
    assert {int(cmd[2:], 16) for cmd in reads} == {
        0x2AB2,
        0x2AB4,
        0x2AB5,
        0x2AB6,
        0x2AB7,
        0x2AB8,
        0x2A53,
        0x2AF7,
        0x15D1,
    }
