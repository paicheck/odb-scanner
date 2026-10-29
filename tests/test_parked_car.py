"""A parked, sleeping car is sent nothing until its 12 V rail says it woke.

A sleeping car fails every read, which used to look like a dead adapter: the
collector reconnected every five minutes all night, and every reconnect put
the 0100 warm-up and a VIN read on the bus of a car that was trying to sleep
(twice, in fact -- the reconnect opened the adapter fully two times). Now the
collector reads only ATRV -- the adapter's own measurement of the 12 V rail,
which puts nothing on the bus -- and resumes when the voltage shows the DC-DC
converter running.

The adapter here is the real Elm327Transport command logic over a scripted
device that records every line sent, so "sent the car nothing" is checked
against the lines themselves: anything not starting with AT is a request the
car's bus would carry.
"""
import socket

import pytest

from config import load_config
from database.repository import Repository
from diagnostic.elm327 import Elm327Transport
from diagnostic.interface import AdapterNotFoundError, CommunicationError
from diagnostic.uds import ProtocolError

SYNTHETIC_VIN = "WVWZZZE1ZMS000001"


class ScriptedAdapter(Elm327Transport):
    """An ELM327 that answers OK to AT commands, NO DATA to the (sleeping)
    car, and ATRV from `volts` in turn -- the last answer repeats, and None
    means the adapter did not answer at all."""

    def __init__(self, *volts):
        super().__init__(host="127.0.0.1", tcp_port=1, timeout=0.1)
        self.volts = list(volts) or ["12.4V"]
        self.sent: list[str] = []
        self.reachable = True

    def open(self):
        if not self.reachable:
            raise AdapterNotFoundError("adapter not reachable")
        self._dev = object()
        self._opened = True
        self._needs_drain = False

    def _write(self, text):
        self.sent.append(text.strip())

    def _read_until_prompt(self, timeout):
        cmd = self.sent[-1]
        if cmd == "ATRV":
            answer = self.volts.pop(0) if len(self.volts) > 1 else self.volts[0]
            if answer is None:
                raise CommunicationError("no answer to ATRV")
            return answer + "\r>"
        if cmd.startswith("AT"):
            return "OK\r>"
        return "NO DATA\r>"

    def to_car(self, since: int = 0) -> list[str]:
        """Every line from `since` on that the car's bus would carry."""
        return [c for c in self.sent[since:] if not c.startswith("AT")]


@pytest.fixture()
def clock(monkeypatch):
    """Fake time: sleeping advances the monotonic clock instantly."""
    now = [1000.0]
    monkeypatch.setattr("collector.time.sleep",
                        lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr("collector.time.monotonic", lambda: now[0])
    return now


@pytest.fixture()
def make(tmp_path):
    """A Collector on a temp database whose adapter is `transport`."""
    from collector import Collector
    made = []

    def _make(transport):
        cfg = load_config()
        cfg._data["database"]["path"] = str(tmp_path / "parked.db")
        cfg._data["adapter"]["type"] = "elm327_tcp"
        cfg._data["collector"]["read_cell_voltages"] = False
        col = Collector(cfg, Repository(cfg._data["database"]["path"]))
        col.conn.t = transport
        col.vehicle_id = col.repo.ensure_vehicle(SYNTHETIC_VIN)
        col.vin = SYNTHETIC_VIN
        made.append(col)
        return col

    yield _make
    for col in made:
        col.repo.close()


def _events(col) -> list[str]:
    return [r["description"] for r in col.repo.conn.execute(
        "SELECT description FROM diagnostic_events ORDER BY id")]


# --- the transport ------------------------------------------------------------

def test_initialize_without_warm_up_sends_the_car_nothing(clock):
    t = ScriptedAdapter()
    t.open()
    t.initialize(warm_up=False)
    assert "ATZ" in t.sent and t.to_car() == []
    t.initialize()
    assert t.to_car() == ["0100"], "the normal initialise must still warm up"


@pytest.mark.parametrize("answer, volts", [
    ("12.6V", 12.6), ("12.6 V", 12.6), ("14.15v", 14.15), ("?", None), ("OK", None),
])
def test_read_voltage_parses_the_atrv_answer(answer, volts):
    t = ScriptedAdapter(answer)
    t.open()
    assert t.read_voltage() == volts
    assert t.sent == ["ATRV"]


def test_read_voltage_raises_when_the_adapter_is_silent():
    t = ScriptedAdapter(None)
    t.open()
    with pytest.raises(CommunicationError):
        t.read_voltage()


def test_the_simulator_reports_an_awake_car():
    from simulator.vehicle import SimServer
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = SimServer("127.0.0.1", port)
    server.start()
    t = Elm327Transport(host="127.0.0.1", tcp_port=port, timeout=5.0)
    try:
        t.open()
        t.initialize(warm_up=False)
        # Above the default wake voltage, so a simulator run never parks.
        assert t.read_voltage() == pytest.approx(14.2)
    finally:
        t.close()
        server.stop()


# --- deciding the car is asleep -----------------------------------------------

@pytest.mark.parametrize("answer, parks", [
    ("12.4V", True),    # adapter fine, car asleep
    ("14.1V", False),   # the car should be answering: reconnect as before
    ("?", False),       # no ATRV on this adapter: reconnect as before
    (None, False),      # the adapter itself is silent: reconnect as before
])
def test_only_a_low_voltage_from_a_live_adapter_means_asleep(make, clock,
                                                             answer, parks):
    t = ScriptedAdapter(answer)
    col = make(t)
    t.open()
    assert col._park_if_asleep() is parks
    assert col._parked is parks


def test_a_sleeping_car_parks_the_collector_and_is_sent_nothing(make, clock,
                                                                monkeypatch):
    t = ScriptedAdapter("12.4V")
    col = make(t)
    t.open()
    monkeypatch.setattr(col, "_start", lambda: None)   # identified already
    start = clock[0]

    col.run(max_cycles=6)          # one dead cycle, then five parked probes

    assert col._parked
    check = t.sent.index("ATRV")   # the ATRV that decided the car is asleep
    assert t.to_car(check) == [], "the parked car was sent a request"
    assert t.sent[check:].count("ATRV") == 6
    assert clock[0] - start >= 5 * col.parked_probe_interval
    events = _events(col)
    assert any(e.startswith("Car asleep: the adapter reads 12.4 V") for e in events)
    assert not any("stopped answering" in e for e in events), (
        "a sleeping car was reported as a lost adapter")


def test_a_car_asleep_at_start_up_starts_parked_instead_of_exiting(make, clock,
                                                                   monkeypatch):
    t = ScriptedAdapter("12.4V")
    col = make(t)
    col.vehicle_id = col.vin = None
    monkeypatch.setattr(col, "discover_ecus",
                        lambda: pytest.fail("discovery run on a sleeping car"))
    col._start()                   # the VIN read gets NO DATA and raises inside
    assert col._parked
    assert not col._discovered


# --- while parked -------------------------------------------------------------

def test_the_parked_collector_resumes_when_the_voltage_rises(make, clock,
                                                             monkeypatch):
    t = ScriptedAdapter("12.4V", "12.3V", "14.1V")
    col = make(t)
    t.open()
    col._enter_parked(12.4)
    calls = []
    monkeypatch.setattr(col, "open_and_identify", lambda: calls.append("identify"))
    monkeypatch.setattr(col, "discover_ecus", lambda: calls.append("discover"))
    col._last_slow = 10_000.0      # pretend a slow phase just ran

    assert col._parked_probe() is False
    assert col._parked_probe() is False
    assert calls == []
    assert col._parked_probe() is True

    assert calls == ["identify", "discover"], "not discovered after waking"
    assert not col._parked
    assert col._last_slow < 0, "slow phase not forced after the parked spell"
    assert any(e.startswith("Car awake: the adapter reads 14.1 V")
               for e in _events(col))


def test_a_high_voltage_with_a_silent_car_backs_off(make, clock, monkeypatch):
    """The car tops up its own 12 V battery now and then: the voltage rises
    but the car stays asleep. Each failed resume doubles the wait, to a cap,
    and a voltage that drops again resets it."""
    from collector import PARKED_RESUME_MAX_HOLD_S
    t = ScriptedAdapter("14.1V")
    col = make(t)
    t.open()
    col._enter_parked(12.4)
    attempts = []

    def identify():
        attempts.append(clock[0])
        raise ProtocolError("NO DATA")

    monkeypatch.setattr(col, "open_and_identify", identify)
    start = clock[0]
    while clock[0] - start < 9000:
        assert col._parked_probe() is False
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    probe = col.parked_probe_interval
    assert gaps == [probe, 2 * probe, 4 * probe, PARKED_RESUME_MAX_HOLD_S]
    assert col._parked

    t.volts = ["12.4V", "14.1V"]   # it settles, then really wakes
    attempts.clear()
    col._parked_probe()
    col._parked_probe()
    assert attempts == [clock[0]], "the hold-off was not reset by a low reading"


def test_a_silent_adapter_is_reopened_without_waking_the_car(make, clock):
    t = ScriptedAdapter(None, "12.3V")
    col = make(t)
    t.open()
    col._enter_parked(12.4)
    mark = len(t.sent)

    assert col._parked_probe() is False

    assert col._parked
    assert "ATZ" in t.sent[mark:], "the adapter was not reopened"
    assert t.to_car(mark) == [], "reopening the adapter warmed up the car's bus"


def test_an_unreachable_adapter_keeps_the_collector_parked(make, clock):
    t = ScriptedAdapter(None)
    col = make(t)
    t.open()
    col._enter_parked(12.4)
    t.reachable = False
    mark = len(t.sent)
    assert col._parked_probe() is False
    assert col._parked
    assert t.to_car(mark) == []


# --- reconnecting -------------------------------------------------------------

def test_reconnecting_to_a_sleeping_car_parks_without_a_warm_up(make, clock,
                                                                monkeypatch):
    t = ScriptedAdapter("12.4V")
    col = make(t)
    monkeypatch.setattr(col, "open_and_identify",
                        lambda: pytest.fail("reconnect woke a sleeping car"))
    assert col._recover_adapter() is False
    assert col._parked
    assert t.to_car() == []


def test_a_reconnect_warms_the_bus_up_once(make, clock, monkeypatch):
    t = ScriptedAdapter("14.1V")
    col = make(t)
    # What the real open_and_identify() starts with.
    monkeypatch.setattr(col, "open_and_identify", lambda: col.conn.open())
    assert col._recover_adapter() is True
    assert t.to_car() == ["0100"], "the reconnect warmed the bus up twice"
