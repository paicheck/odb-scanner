"""The MEB experiment matrix must work before it is pointed at the car.

tools/diagnose_meb_path.py exists to find out why this ID.3 answers NO DATA to
the BMS. A matrix that has never executed is not evidence, it is a guess with
print statements: if it built the wrong header, mis-padded the frame, or
mis-parsed the answer, every experiment would report NO DATA on the car and the
conclusion drawn would be about the harness rather than the vehicle.

So it is run here against the built-in simulator, which implements the MEB
module map with the real addresses and MEB-scaled values. The assertions are
about the transport behaviour, not about the simulated engineering values:

  * 29-bit modules answer on their 17FExxxx response header, not functionally
  * the 11-bit modules answer on their 3-digit header
  * the ISO-TP single frame this project builds by hand is understood
  * a module that is not addressed does NOT answer

That last one matters most. An experiment matrix that answers for everything
would prove nothing when run against the car.
"""
import socket

import pytest


@pytest.fixture()
def sim_port():
    """A simulator on a free port, closed before cleanup.

    Windows needs the listening socket released before the temp dir can be
    removed, so the generator closes it in the finally path.
    """
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


def transport(port):
    from diagnostic.elm327 import Elm327Transport
    t = Elm327Transport(host="127.0.0.1", tcp_port=port, timeout=5.0)
    t.open()
    t.initialize()
    return t


def addressed(t, cp, sh, caf=0):
    """Point the adapter at one module the way set_module() does."""
    if cp is not None:
        t.send_command(f"ATCP {cp:02X}")
    t.send_command(f"ATSH {sh}")
    if caf is not None:
        t.send_command(f"ATCAF{caf}")


def read(t, hexcmd: str) -> dict:
    """Send a request and return reassembled payloads keyed by sender."""
    from diagnostic import uds
    wire = (f"{len(hexcmd) // 2:02X}{hexcmd}" + "55" * 8)[:16]
    return uds.payloads_by_source(t.send_command(wire))


@pytest.mark.parametrize("did,expect_first_byte", [
    ("22028C", 0x62),   # SoC (BMS)
    ("221E3B", 0x62),   # pack voltage
    ("221E3D", 0x62),   # pack current (4 data bytes)
    ("222A0B", 0x62),   # battery temperature
    ("221E0E", 0x62),   # max temp
    ("221E0F", 0x62),   # min temp
])
def test_bms_answers_on_its_29_bit_header(sim_port, did, expect_first_byte):
    """EXP-5: the BMS answers a hand-built single frame at 0x17FC007B.

    The DID must be echoed back and the positive-response byte 0x62 must lead.
    """
    t = transport(sim_port)
    try:
        addressed(t, 0x17, "FC007B")
        payloads = read(t, did)
    finally:
        t.close()

    positive = [p for p in payloads.values() if p]
    assert positive, f"{did} got no answer at all: {payloads}"
    assert positive[0][0] == expect_first_byte, positive[0].hex()
    assert positive[0][1:3] == bytes.fromhex(did[2:]), \
        f"DID echo mismatch: {positive[0].hex()}"
    assert 0x17FE007B in payloads, \
        f"expected the 29-bit BMS response header, got {list(payloads)}"


@pytest.mark.parametrize("did", ["222AB2", "222AB8", "222AF7"])
def test_energy_module_answers_on_its_11_bit_header(sim_port, did):
    """EXP-9: the energy module is 11-bit and answers at 0x77A.

    Car Scanner's hv_energy_content comes from here, not from the BMS, so this
    arm is a separate target from the 29-bit one.
    """
    t = transport(sim_port)
    try:
        t.send_command("ATSP6")
        addressed(t, 0x00, "000710")
        payloads = read(t, did)
    finally:
        t.close()

    positive = [p for p in payloads.values() if p]
    assert positive, f"{did} got no answer at 0x710: {payloads}"
    assert positive[0][0] == 0x62
    assert positive[0][1:3] == bytes.fromhex(did[2:])
    assert 0x77A in payloads, \
        f"expected the 11-bit 0x77A header, got {list(payloads)}"


@pytest.mark.parametrize("did", ["22465B", "22465D"])
def test_dcdc_answers_on_its_29_bit_header(sim_port, did):
    """EXP-10: the DC/DC module answers at 0x17FC00B9 -> 0x17FE00B9."""
    t = transport(sim_port)
    try:
        addressed(t, 0x17, "FC00B9")
        payloads = read(t, did)
    finally:
        t.close()

    positive = [p for p in payloads.values() if p]
    assert positive, f"{did} got no answer at 0xB9: {payloads}"
    assert positive[0][0] == 0x62
    assert 0x17FE00B9 in payloads, list(payloads)


def test_atbi_is_accepted_and_does_not_break_addressing(sim_port):
    """EXP-6: ATBI is a no-op for the sim but must not disturb the module.

    On the real clone this is the top candidate, so the arm has to be known to
    leave addressing usable rather than assumed safe.
    """
    t = transport(sim_port)
    try:
        t.send_command("ATBI")
        addressed(t, 0x17, "FC007B")
        payloads = read(t, "22028C")
    finally:
        t.close()

    assert any(p and p[0] == 0x62 for p in payloads.values()), payloads


def test_session_and_tester_present_do_not_break_addressing(sim_port):
    """EXP-7 / 7b / 8: 10 01 and 3E 00 must leave the BMS readable.

    Neither is strictly required by evDash, so if a future run finds the BMS
    needs one this is where that finding would be built on. Guarding them here
    means an arm can fail without the harness itself being suspect.
    """
    t = transport(sim_port)
    try:
        addressed(t, 0x17, "FC007B")
        for cmd in ("0210015555555555555555", "023E005555555555555555"):
            t.send_command(cmd)
        payloads = read(t, "221E3B")
    finally:
        t.close()

    assert any(p and p[0] == 0x62 for p in payloads.values()), payloads


def test_unaddressed_module_does_not_answer(sim_port):
    """The negative control, and the reason to trust the rest.

    Pointing the adapter at a module that does not exist must produce no
    answer. If every arm answered, a NO DATA on the car would say nothing about
    the car.
    """
    t = transport(sim_port)
    try:
        addressed(t, 0x17, "FC9999")       # no module at 0x17FC9999
        payloads = read(t, "22028C")
    finally:
        t.close()

    assert not [p for p in payloads.values() if p], \
        f"an unaddressed module answered: {payloads}"


def test_functional_read_does_not_reach_the_bms(sim_port):
    """EXP-11: functional 22 028C must NOT return the BMS's answer.

    This is what the collector's discovery relies on: if functional addressing
    reached the BMS there would be no addressing problem to solve at all.
    """
    t = transport(sim_port)
    try:
        t.send_command("ATCP 18")
        t.send_command("ATSH DB33F1")
        t.send_command("ATCAF1")
        payloads = read(t, "22028C")
    finally:
        t.close()

    did_echo = [p for p in payloads.values()
                if p and p[0] == 0x62 and p[1:3] == b"\x02\x8C"]
    assert not did_echo, f"functional addressing reached the BMS: {did_echo}"


def test_experiment_matrix_runs_end_to_end(sim_port, capsys):
    """The whole tool, top to bottom, against the sim.

    Asserting it completes is the point: a NameError or a bad AT sequence three
    experiments in would otherwise only surface with the car plugged in.
    """
    from tools.diagnose_meb_path import run_all_experiments

    run_all_experiments(tcp=("127.0.0.1", sim_port))
    out = capsys.readouterr().out
    for exp in ("EXP-1", "EXP-4", "EXP-5", "EXP-9", "EXP-13"):
        assert exp in out, f"{exp} never ran"
