"""Process-management tests for tools/start_scanner.py.

Everything here is mocked: no process is started, none is killed, and no COM
port is touched. The scenarios are the ways stack.json and a live pid can
disagree, because that disagreement is what makes this script dangerous:
it runs `taskkill /PID <n> /T /F`, which takes out a whole process tree.

A pid is not an identity. stack.json survives a reboot, a Ctrl-C and a crash,
and Windows reissues low pids quickly, so "some process has that pid" is not
evidence that it is ours.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import start_scanner as ss  # noqa: E402


def _state_at(tmp_path, monkeypatch):
    """Point the module's state file and log dir into a temp directory."""
    monkeypatch.setattr(ss, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ss, "STATE", tmp_path / "stack.json")
    return tmp_path / "stack.json"


def _no_real_kills(monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(ss, "_kill_tree", lambda pid: killed.append(pid))
    return killed


def test_stop_refuses_a_pid_whose_identity_does_not_match(tmp_path,
                                                          monkeypatch):
    """The recorded pid is alive, but it belongs to something else now.

    Without an identity check this is a `taskkill /T /F` against an unrelated
    process and every one of its children -- a terminal, an IDE, a build --
    reported to the operator as "stopped collector".
    """
    state = _state_at(tmp_path, monkeypatch)
    state.write_text(json.dumps(
        {"collector": {"pid": 15964, "identity": "before-the-reboot"}}),
        encoding="utf-8")

    killed = _no_real_kills(monkeypatch)
    monkeypatch.setattr(ss, "_alive", lambda pid: True)
    monkeypatch.setattr(ss, "_process_identity", lambda pid: "someone-else")

    assert ss.cmd_stop() == 0
    assert killed == [], "killed a process this script did not start"
    # The record is proven stale, so it is dropped rather than retried forever.
    assert not state.exists()


def test_stop_kills_only_when_the_identity_matches(tmp_path, monkeypatch):
    state = _state_at(tmp_path, monkeypatch)
    state.write_text(json.dumps(
        {"collector": {"pid": 4242, "identity": "created-at-T"}}),
        encoding="utf-8")

    killed = _no_real_kills(monkeypatch)
    alive = {"4242": True}
    monkeypatch.setattr(ss, "_alive", lambda pid: alive[str(pid)])
    monkeypatch.setattr(ss, "_process_identity", lambda pid: "created-at-T")

    def kill(pid):
        killed.append(pid)
        alive[str(pid)] = False          # the component actually dies

    monkeypatch.setattr(ss, "_kill_tree", kill)

    assert ss.cmd_stop() == 0
    assert killed == [4242]
    assert not state.exists(), "a fully stopped stack should clear its state"


def test_stop_keeps_the_state_of_a_component_that_refused_to_die(tmp_path,
                                                                 monkeypatch):
    """A failed kill must not also lose the pid.

    The file was deleted unconditionally, so after a failed taskkill the next
    start saw no collector recorded and launched a second one -- against a
    serial port the first was still holding.
    """
    state = _state_at(tmp_path, monkeypatch)
    state.write_text(json.dumps(
        {"collector": {"pid": 4242, "identity": "created-at-T"},
         "web": {"pid": 77, "identity": "web-T"}}), encoding="utf-8")

    _no_real_kills(monkeypatch)          # kills do nothing: nothing dies
    monkeypatch.setattr(ss, "_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(ss, "_process_identity",
                        lambda pid: "created-at-T" if pid == 4242 else "web-T")

    class _Clock:
        """Advances only when the code under test sleeps, so the 10 s grace
        period costs nothing and still terminates."""

        def __init__(self):
            self.now = 1000.0

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    monkeypatch.setattr(ss, "time", _Clock())

    assert ss.cmd_stop() == 1
    kept = json.loads(state.read_text(encoding="utf-8"))
    assert kept["collector"]["pid"] == 4242
    assert "web" not in kept, "a component that was already gone was retained"


def test_stop_will_not_kill_a_state_file_that_predates_identities(tmp_path,
                                                                  monkeypatch):
    """Old format: {"collector": 1234}. It may be ours; it cannot be proven."""
    state = _state_at(tmp_path, monkeypatch)
    state.write_text(json.dumps({"collector": 1234}), encoding="utf-8")

    killed = _no_real_kills(monkeypatch)
    monkeypatch.setattr(ss, "_alive", lambda pid: True)
    monkeypatch.setattr(ss, "_process_identity", lambda pid: "whatever")

    assert ss.cmd_stop() == 1
    assert killed == [], "killed a pid that could not be verified"
    # Unverifiable is not the same as stale: the record is kept so the
    # component stays visible to --status instead of becoming an orphan.
    assert json.loads(state.read_text(encoding="utf-8"))["collector"]["pid"] \
        == 1234


def test_state_round_trips_and_tolerates_the_old_shape(tmp_path, monkeypatch):
    state = _state_at(tmp_path, monkeypatch)

    ss._save_state({"web": {"pid": 9, "identity": "x"}})
    assert ss._load_state() == {"web": {"pid": 9, "identity": "x"}}

    state.write_text(json.dumps({"collector": 1234}), encoding="utf-8")
    assert ss._load_state() == {"collector": {"pid": 1234, "identity": None}}

    # Truncated JSON must not crash the caller, and must not be half-read.
    state.write_text('{"collector": {"pid": 12', encoding="utf-8")
    assert ss._load_state() == {}


def test_a_live_process_with_a_foreign_identity_is_not_ours(monkeypatch):
    monkeypatch.setattr(ss, "_alive", lambda pid: True)
    monkeypatch.setattr(ss, "_process_identity", lambda pid: "A")

    assert ss._is_ours({"pid": 10, "identity": "A"}) is True
    assert ss._is_ours({"pid": 10, "identity": "B"}) is False
    assert ss._is_ours({"pid": 10, "identity": None}) is False
    assert ss._is_ours(None) is False


# --- which database a run writes to ------------------------------------------
def _start_config(adapter_type: str):
    from config import Config
    return Config({
        "adapter": {"type": adapter_type, "port": "COM3",
                    "tcp_host": "127.0.0.1", "tcp_port": 35000},
        "simulation": {"host": "127.0.0.1", "port": 35000,
                       "db_path": "data/simulator.db"},
        "database": {"path": "data/car.db"},
        "web": {"host": "127.0.0.1", "port": 8000},
    })


def _fake_start(tmp_path, monkeypatch):
    """Intercept every spawn. No process starts, no port is probed."""
    _state_at(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "LOCK", tmp_path / "start.lock")
    monkeypatch.setattr(ss, "ROOT", tmp_path.parent)
    monkeypatch.setattr(ss, "_process_identity", lambda pid: "fake")
    monkeypatch.setattr(ss, "_wait_port", lambda host, port: True)
    monkeypatch.setattr(ss, "_port_open", lambda host, port: False)
    monkeypatch.setattr(ss, "_is_ours", lambda entry: False)

    spawned: dict[str, dict] = {}

    def spawn(name, args, extra_env=None):
        spawned[name] = {"args": args, "env": extra_env}
        return 100 + len(spawned)

    monkeypatch.setattr(ss, "_spawn", spawn)
    return spawned


def test_a_simulator_run_is_pointed_at_the_simulators_own_database(
        tmp_path, monkeypatch):
    """The collector's database is chosen here, so it is chosen here wrongly.

    This is the step that put the simulator's readings into the car's file: the
    simulator reports a VIN and the collector keys its rows by whatever
    `database.path` says, so nothing downstream could tell the two apart.
    """
    spawned = _fake_start(tmp_path, monkeypatch)
    assert ss.cmd_start(_start_config("elm327_tcp"), None, with_web=False) == 0
    assert spawned["simulator"]["env"] is None
    assert spawned["collector"]["env"] == {"ODB_DB_PATH": "data/simulator.db"}


def test_a_real_car_run_writes_to_the_configured_database(tmp_path, monkeypatch):
    """An override that leaked into the real-car path would send measured data
    to the simulator's file instead -- the same mix-up, pointing the other way."""
    spawned = _fake_start(tmp_path, monkeypatch)
    assert ss.cmd_start(_start_config("elm327_serial"), None,
                        with_web=False) == 0
    assert "simulator" not in spawned, "started a simulator for a real adapter"
    assert spawned["collector"]["env"] is None
