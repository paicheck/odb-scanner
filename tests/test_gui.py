"""Tests for the desktop GUI's non-widget logic.

The widgets themselves need a display, but everything that can go wrong
silently is not a widget: the action table pointing at a script that does not
exist, a command that fails being reported as success, the database being
unreadable, or two commands running at once and fighting over the serial port.

No test here needs a car, an adapter, or a visible window.
"""
import os
import queue
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tk = pytest.importorskip("tkinter", reason="GUI tests need a Tk build")

from database.repository import Repository  # noqa: E402
from gui.app import (  # noqa: E402
    ACTIONS,
    CommandRunner,
    load_state,
)

SYNTHETIC_VIN = "WVWZZZE1ZMS000001"


@pytest.fixture()
def gui_db(tmp_path, monkeypatch):
    """A seeded temp database that load_state() reads instead of the real one.

    load_state() loads the real config, so without ODB_DB_PATH it opened
    database.path -- on the owner's PC the car's database, or data/real_car.db
    without a config.local.yaml -- with a Repository that runs the schema
    script on it. One key is read three times, so a view that counts rows
    instead of keys shows 3 where it should show 1.
    """
    db = tmp_path / "gui.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle(SYNTHETIC_VIN)
        for minute in range(3):
            repo.record_measurement(
                vid, f"2026-10-05T18:0{minute}:00+00:00", "soc_abs",
                "bat_mgmt", "UDS-0x22", "0x028C", "%",
                "imported: Car Scanner CSV", "identity",
                "experimentally determined", "AB", value=50.0 + minute)
    monkeypatch.setenv("ODB_DB_PATH", str(db))
    return db


def _drain(q, codes, timeout=60):
    """Collect streamed output until on_exit fires, then flush the buffer."""
    lines = []
    deadline = time.time() + timeout
    while time.time() < deadline and not codes:
        try:
            lines.append(q.get(timeout=0.5))
        except queue.Empty:
            continue
    while True:
        try:
            lines.append(q.get_nowait())
        except queue.Empty:
            break
    return lines


# --- the action table --------------------------------------------------------

@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: a["label"])
def test_every_action_points_at_a_script_that_exists(action):
    """A button wired to a missing script is a no-op that looks like it worked.

    Checked per action so adding one without its target fails here rather than
    in front of the user.
    """
    assert action["argv"], f"{action['label']} has no command"
    script = action["argv"][0]
    assert (ROOT / script).exists(), f"{script} does not exist"
    for key in ("label", "help", "ok", "fail"):
        assert action.get(key), f"{action['label']} is missing {key!r}"


def test_actions_cover_every_bat_shortcut():
    """The GUI replaces the .bat files, so each one's command must be present.

    If a shortcut is dropped, the user loses a capability with no indication --
    and the ones most likely to be missed are exactly the recovery ones (stop,
    status) that matter when something has gone wrong.
    """
    argvs = [" ".join(a["argv"]) for a in ACTIONS]
    expected = [
        "main.py doctor",
        "main.py doctor --all-ports",
        "tools/start_scanner.py",
        "tools/start_scanner.py --stop",
        "tools/start_scanner.py --status",
        "tools/smoke_test.py",
    ]
    for cmd in expected:
        assert cmd in argvs, f"no action runs {cmd!r}; got {argvs}"


def test_no_action_issues_a_raw_vehicle_request():
    """The GUI launches the project's own entry points and nothing else.

    Every action is a script that already exists and already routes through
    uds.validate_request. A button that sent hex at the adapter directly would
    be a hole in the read-only guarantee, so the argv is pinned to project
    scripts rather than to an interpreter snippet.
    """
    for action in ACTIONS:
        argv = action["argv"]
        assert argv[0].endswith(".py"), (
            f"{action['label']} runs {argv[0]!r}, not a project script")
        assert not any(a.startswith("-c") for a in argv), (
            f"{action['label']} runs inline code, which bypasses the "
            "read-only choke point")
        assert (ROOT / argv[0]).exists()


def test_stop_and_status_stay_clickable_while_busy():
    """The recovery actions must not be disabled by a hung command.

    `always_enabled` exists so a doctor that never returns can still be
    stopped and the status checked. Disabling every button during a run would
    make a hang unrecoverable from the GUI.
    """
    labels = {a["label"] for a in ACTIONS if a.get("always_enabled")}
    assert "Stop" in labels
    assert "Status" in labels


# --- the database view -------------------------------------------------------

def test_load_state_reports_per_key_not_per_row(gui_db):
    """The counts must match the web /sources page, which counts registry keys.

    An earlier version counted measurement rows, so 1959 rows of ordinary
    history were reported as 1959 stale measurements and the table listed one
    imported key six times. Two views of one database disagreeing is worse than
    either being wrong on its own.
    """
    rows, counts, vin, note = load_state()
    assert note == "", note
    assert vin == SYNTHETIC_VIN, "read a database other than the seeded one"
    assert rows, "expected one row per registry key"
    # Keys are unique: a per-key view cannot list the same measurement twice.
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), "duplicate keys: this is a per-row view"
    assert counts["imported"] == 1, "three rows of one key must count once"
    assert counts["total"] == len(rows)
    for state in ("live", "imported", "stale", "unavailable"):
        assert counts[state] >= 0
    assert sum(counts[s] for s in
               ("live", "imported", "stale", "unavailable")) == counts["total"]


def test_load_state_explains_a_broken_database_instead_of_crashing(monkeypatch):
    """A missing or corrupt database must produce a message, not a traceback.

    The GUI is often the first thing opened, before the collector has ever run,
    so an empty or absent database is a normal state rather than an error -- and
    zeros that look like data would be worse than either.
    """
    import config as config_mod

    def boom(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(config_mod, "load_config", boom)
    rows, counts, vin, note = load_state()
    assert rows == [] and counts == {} and vin == ""
    assert "database is locked" in note


# --- the command runner ------------------------------------------------------

def test_runner_streams_output_and_reports_a_zero_exit():
    """Output must arrive while the command runs, not only at the end.

    The doctor takes about a minute; a runner that buffered until exit would
    look frozen for that whole time.
    """
    q: queue.Queue[object] = queue.Queue()
    codes: list = []
    runner = CommandRunner(q)
    assert runner.start(["-c", "print('first'); print('second')"], codes.append)
    lines = _drain(q, codes)
    assert codes == [0], f"expected one callback with 0, got {codes}"
    joined = "".join(lines)
    assert "first" in joined and "second" in joined
    assert "[exit code 0]" in joined


def test_runner_reports_a_non_zero_exit_as_failure():
    """A failing command must never be presented as success.

    This is the difference between the verdict banner saying 'pipeline OK' and
    saying 'something failed' -- reporting 0 for a crash would send the user
    looking at the car instead of at the software.
    """
    q: queue.Queue[object] = queue.Queue()
    codes: list = []
    CommandRunner(q).start(["-c", "import sys; sys.exit(3)"], codes.append)
    lines = _drain(q, codes, timeout=30)
    assert codes == [3], f"expected exit 3, got {codes}"
    assert any("[exit code 3]" in ln for ln in lines)


def test_runner_reports_a_command_that_cannot_be_launched():
    """A missing script is a launch failure, and must still call back.

    Without the callback the buttons stay disabled forever and the window has
    to be killed.
    """
    q: queue.Queue[object] = queue.Queue()
    codes: list = []
    CommandRunner(q).start(["no_such_script_xyz.py"], codes.append)
    lines = _drain(q, codes, timeout=30)
    assert codes and codes[0] != 0
    joined = "".join(lines)
    assert "launcher error" in joined or "[exit code" in joined


def test_runner_refuses_a_second_command_while_busy():
    """Two commands at once would fight over the serial port.

    The collector holds the adapter open; a doctor started alongside it would
    fail to open the port and report an adapter fault that is really a
    self-inflicted contention.
    """
    q: queue.Queue[object] = queue.Queue()
    codes: list = []
    runner = CommandRunner(q)
    # A short sleep keeps the runner busy deterministically without spawning a
    # long-lived child that has to be killed.
    assert runner.start(["-c", "import time; time.sleep(1.5)"], codes.append)
    assert runner.busy
    assert runner.start(["-c", "print('nope')"], codes.append) is False
    deadline = time.time() + 30
    while runner.busy and time.time() < deadline:
        time.sleep(0.1)
    assert codes == [0], f"the first command should still complete: {codes}"


def test_runner_terminate_does_not_raise_when_nothing_is_running():
    """Closing the window before any command runs must be clean."""
    runner = CommandRunner(queue.Queue())
    assert runner.busy is False
    runner.terminate()          # must not raise


def test_runner_sets_utf8_so_output_cannot_fail_to_decode():
    """The doctor prints box-drawing characters and degree signs.

    Under a cp1252 console code page those raise on decode, which would abort a
    run part-way and leave the verdict unread -- a failure that looks like an
    adapter fault.
    """
    q: queue.Queue[object] = queue.Queue()
    codes: list = []
    captured: dict = {}

    real_popen = __import__("subprocess").Popen

    def spy_popen(*args, **kwargs):
        captured.update(kwargs.get("env") or {})
        return real_popen(*args, **kwargs)

    import subprocess as subprocess_mod
    original = subprocess_mod.Popen
    subprocess_mod.Popen = spy_popen
    try:
        runner = CommandRunner(q)
        runner.start(["-c", "print('\\u00b0C \\u2500 box')"], codes.append)
        _drain(q, codes, timeout=30)
    finally:
        subprocess_mod.Popen = original

    assert captured.get("PYTHONIOENCODING") == "utf-8"
    assert captured.get("PYTHONUTF8") == "1"
    assert codes == [0]


# --- the launch path a user actually takes -----------------------------------

def test_gui_runs_as_a_script_from_a_foreign_working_directory(tmp_path, gui_db):
    """`python gui\\app.py` must work when nothing else put the root on the path.

    Regression, and the reason --selftest exists. Run as a script, sys.path[0]
    is the script's own directory, so `import analysis` failed and the window
    opened showing "Could not read the database: No module named 'analysis'".
    Every test passed anyway: conftest.py inserts the repo root, and each
    headless check imported gui.app after doing the same. The app was only
    broken the way a user launches it.

    So this runs it as a real subprocess, with cwd somewhere else and a
    PYTHONPATH that does NOT include the project -- reproducing the double-click
    / Start-Process case rather than the in-process one.
    """
    import subprocess

    # The seeded database, named relative to the project root the way
    # config.yaml names database.path ("data/real_car.db"), so only a launch
    # that resolves it against the root finds it.
    try:
        db_arg = os.path.relpath(gui_db, ROOT)
    except ValueError:  # Windows: temp dir and checkout on different drives
        db_arg = str(gui_db)

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)          # must not smuggle the root in
    env["PYTHONIOENCODING"] = "utf-8"
    env["ODB_DB_PATH"] = db_arg
    proc = subprocess.run(
        [sys.executable, str(ROOT / "gui" / "app.py"), "--selftest"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "No module named" not in out, out
    assert proc.returncode == 0, f"exit {proc.returncode}: {out}"
    assert "SELFTEST OK" in out, out

    # And it must read the SAME database the repo-root launch reads. A second
    # bug hid behind the first: database.path is relative ("data/real_car.db"),
    # so a foreign cwd resolved it against the wrong directory, created an empty
    # database there, and reported every key as "unavailable" -- which reads as
    # "this car never reported this" rather than "this is the wrong database".
    # Comparing the two runs is the invariant; either count on its own is only
    # meaningful relative to the other.
    root_env = dict(os.environ)
    root_env.pop("PYTHONPATH", None)
    root_env["PYTHONIOENCODING"] = "utf-8"
    root_env["ODB_DB_PATH"] = db_arg
    from_root = subprocess.run(
        [sys.executable, str(ROOT / "gui" / "app.py"), "--selftest"],
        cwd=str(ROOT), env=root_env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    root_out = (from_root.stdout or "") + (from_root.stderr or "")
    assert "SELFTEST OK" in root_out, root_out

    def counts_of(text: str) -> str:
        return text.split("counts=", 1)[1].strip() if "counts=" in text else ""

    # The seeded key, so two runs that both missed it cannot agree on zeros.
    assert "'imported': 1" in counts_of(root_out), root_out
    assert counts_of(out) == counts_of(root_out), (
        "a foreign working directory read a different database:\n"
        f"  foreign cwd : {counts_of(out)}\n"
        f"  repo root   : {counts_of(root_out)}")
