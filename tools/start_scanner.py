"""Start / stop / inspect the odb_scanner stack (simulator + collector + web).

The three components are independent processes (see README "Manual
equivalent, in two terminals"): this script just starts them detached with
logs in data/logs/ and a pid file, so it can be re-run safely — anything that
is already running is skipped instead of duplicated.

Usage:
    python tools/start_scanner.py                 # sim + collector + dashboard
    python tools/start_scanner.py --cycles 1      # one pass, then stop collecting
    python tools/start_scanner.py --no-web        # adapter + collector only
    python tools/start_scanner.py --status        # what is running right now
    python tools/start_scanner.py --stop          # stop everything this script started

Logs:  data/logs/{simulator,collector,web}.log
State: data/logs/stack.json (pids)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402

LOG_DIR = ROOT / "data" / "logs"
STATE = LOG_DIR / "stack.json"
LOCK = LOG_DIR / "start.lock"

# Ceiling for the raw output files (tracebacks, uvicorn) that this launcher
# captures itself. The real logs are rotated by main.py; these only need a
# bound so a long-lived component cannot fill the disk.
RAW_LOG_MAX_BYTES = 2 * 1024 * 1024
LOCK_STALE_S = 60.0


# --- process helpers ---------------------------------------------------------
def _python() -> str:
    """Prefer the project virtualenv interpreter when it exists."""
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe",
                      ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _probe_host(host: str) -> str:
    """A service bound to all interfaces is still probed on loopback."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _wait_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(0.25)
    return False


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # OpenProcess + GetExitCodeProcess (no psutil needed)
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:  # pragma: no cover - posix
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _spawn(name: str, args: list[str],
           extra_env: dict[str, str] | None = None) -> int:
    """Start a component detached so it survives this script exiting."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # Archive the previous run's raw output rather than appending forever. This
    # file only holds what Python's logging does NOT own -- tracebacks, and
    # uvicorn's own handler output -- because main.py now writes the real log
    # itself with rotation on write. Appending here meant collector.log grew
    # without bound and every run buried the last one.
    raw = LOG_DIR / f"{name}.out"
    if raw.exists() and raw.stat().st_size > RAW_LOG_MAX_BYTES:
        archive = raw.with_suffix(f".{time.strftime('%Y%m%dT%H%M%S')}.log")
        try:
            raw.replace(archive)
        except OSError:
            raw.unlink(missing_ok=True)
    log = raw.open("ab")
    try:
        proc = subprocess.Popen(
            [_python(), *args], cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
            # Tells main.py that stderr already goes to a file we manage, so it
            # does not add a console handler and write every line twice.
            env={**os.environ, "ODB_STDERR_CAPTURED": "1",
                 **(extra_env or {})})
    finally:
        log.close()
    return proc.pid


def _process_identity(pid: int) -> str | None:
    """A token distinguishing THIS process from a later one reusing its id.

    A pid on its own is not an identity. stack.json outlives a reboot, a
    Ctrl-C and a crash, and Windows reissues low pids quickly, so a --stop run
    after a reboot could find a recorded pid "alive", belonging to something
    else entirely, and taskkill /T it along with every one of its children. A
    terminal, an IDE or a build would simply vanish, and the script would
    report "stopped collector".

    The process creation time is the cheap standard answer: it is fixed for
    the life of a process and a recycled pid gets a different one. Returns
    None when it cannot be read, which callers must treat as "cannot verify",
    never as "matches".
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited = wintypes.FILETIME(), wintypes.FILETIME()
            kernel, user = wintypes.FILETIME(), wintypes.FILETIME()
            if not k32.GetProcessTimes(handle, ctypes.byref(created),
                                       ctypes.byref(exited),
                                       ctypes.byref(kernel),
                                       ctypes.byref(user)):
                return None
            return f"{created.dwHighDateTime}:{created.dwLowDateTime}"
        finally:
            k32.CloseHandle(handle)
    try:  # pragma: no cover - posix
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            # Field 22 (starttime). The comm field can contain spaces, so the
            # split has to start after its closing parenthesis.
            return fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _is_ours(entry: dict | None) -> bool:
    """True only when the recorded process is still the one we started."""
    if not entry:
        return False
    pid = entry.get("pid", 0)
    if not _alive(pid):
        return False
    recorded = entry.get("identity")
    if recorded is None:
        # Written by a build that did not record one. Unverifiable, so it is
        # not treated as ours -- killing the wrong tree is worse than leaving
        # a process for the operator to stop by hand.
        return False
    return recorded == _process_identity(pid)


def _load_state() -> dict:
    """name -> {"pid": int, "identity": str | None}.

    Accepts the older `{"collector": 1234}` shape so an upgrade does not crash
    on a state file written by the previous version; those entries carry no
    identity and are therefore unverifiable.
    """
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
    out: dict = {}
    for name, value in (data or {}).items():
        try:
            if isinstance(value, dict):
                out[name] = {"pid": int(value["pid"]),
                             "identity": value.get("identity")}
            else:
                out[name] = {"pid": int(value), "identity": None}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _record(pid: int) -> dict:
    return {"pid": pid, "identity": _process_identity(pid)}


def _save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Written via a temp file: a crash partway through a direct write leaves
    # truncated JSON, which _load_state swallows as {} -- losing every pid and
    # making the running stack unstoppable by this script.
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


# --- commands ----------------------------------------------------------------
def cmd_start(cfg, cycles: int | None, with_web: bool) -> int:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < LOCK_STALE_S:
        print("Another start is already in progress (data/logs/start.lock).")
        return 1
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="ascii")

    state = _load_state()
    started: list[str] = []
    skipped: list[str] = []
    try:
        sim_host = cfg.get("simulation.host", "127.0.0.1")
        sim_port = int(cfg.get("simulation.port", 35000))
        sim_mode = cfg.get("adapter.type", "elm327_tcp") == "elm327_tcp"
        if sim_mode:
            if _is_ours(state.get("simulator")) \
                    or _port_open(sim_host, sim_port):
                skipped.append(f"simulator already up on {sim_host}:{sim_port}")
            else:
                pid = _spawn("simulator", ["main.py", "simulate"])
                state["simulator"] = _record(pid)
                ok = _wait_port(sim_host, sim_port)
                started.append(
                    f"simulator pid {pid} "
                    + (f"listening on {sim_host}:{sim_port}" if ok else
                       f"FAILED to listen on {sim_host}:{sim_port} "
                       "(see data/logs/simulator.log)"))
        else:
            skipped.append("simulator not needed "
                           f"(adapter.type = {cfg.get('adapter.type')})")

        if _is_ours(state.get("collector")):
            skipped.append("collector already running "
                           f"(pid {state['collector']['pid']})")
        else:
            args = ["main.py", "collect"]
            if cycles is not None:
                args += ["--cycles", str(cycles)]
            # Point a simulator run at its own database. The simulator answers
            # with a demo VIN, but the collector would still key its readings to
            # whatever database.path says, and synthetic rows sitting beside
            # measured ones cannot be told apart afterwards -- this is how a
            # month of simulated readings became part of the car's history.
            env = {"ODB_DB_PATH": cfg.simulator_db_path} if sim_mode else None
            pid = _spawn("collector", args, extra_env=env)
            state["collector"] = _record(pid)
            target = cfg.simulator_db_path if sim_mode else cfg.db_path
            started.append(f"collector pid {pid} -> {target} "
                           f"(log: data/logs/collector.log)")

        web_host = cfg.get("web.host", "127.0.0.1")
        web_port = int(cfg.get("web.port", 8000))
        if not with_web:
            skipped.append("web dashboard disabled (--no-web)")
        elif _is_ours(state.get("web")) \
                or _port_open(_probe_host(web_host), web_port):
            skipped.append(f"web dashboard already up on "
                           f"http://{_probe_host(web_host)}:{web_port}")
        else:
            pid = _spawn("web", ["main.py", "serve"])
            state["web"] = _record(pid)
            ok = _wait_port(_probe_host(web_host), web_port)
            started.append(
                f"web pid {pid} "
                + (f"-> http://{_probe_host(web_host)}:{web_port}" if ok else
                   f"FAILED to listen on {web_host}:{web_port} "
                   "(see data/logs/web.log)"))
    finally:
        # Saved here, not after the try: if a _spawn raises partway through,
        # the components already started would otherwise be running with no
        # recorded pid at all -- invisible to --status, unstoppable by --stop,
        # and still holding the serial port. Recovery was Task Manager.
        _save_state(state)
        LOCK.unlink(missing_ok=True)
    for line in started:
        print(f"  started: {line}")
    for line in skipped:
        print(f"  skipped: {line}")
    print(f"\nLogs in {LOG_DIR.relative_to(ROOT)}  |  "
          "stop with: python tools/start_scanner.py --stop")
    return 1 if any("FAILED" in line for line in started) else 0


def cmd_status(cfg) -> int:
    state = _load_state()
    sim_host = cfg.get("simulation.host", "127.0.0.1")
    sim_port = int(cfg.get("simulation.port", 35000))
    sim_mode = cfg.get("adapter.type", "elm327_tcp") == "elm327_tcp"
    web_host = cfg.get("web.host", "127.0.0.1")
    web_port = int(cfg.get("web.port", 8000))
    rows = [
        ("simulator", state.get("simulator"),
         f"{sim_host}:{sim_port} listening={_port_open(sim_host, sim_port)}"),
        # The collector's database is not always database.path: a simulator run
        # is pointed at its own file, so report the one it is actually using.
        ("collector", state.get("collector"),
         f"db={cfg.simulator_db_path if sim_mode else cfg.db_path}"
         + (f" (simulator mode; database.path is {cfg.db_path})"
            if sim_mode else "")),
        ("web", state.get("web"),
         f"{_probe_host(web_host)}:{web_port} "
         f"listening={_port_open(_probe_host(web_host), web_port)}"),
    ]
    for name, entry, extra in rows:
        if entry is None:
            print(f"  {name:<10} not started by this script   {extra}")
            continue
        pid = entry["pid"]
        if _is_ours(entry):
            state_text = "alive=True"
        elif _alive(pid):
            # Something holds the pid, but not the process we started. Say so
            # rather than reporting it as ours and offering to kill it.
            state_text = "alive=True (DIFFERENT process - pid was reused)"
        else:
            state_text = "alive=False"
        print(f"  {name:<10} pid {pid:<6} {state_text}   {extra}")
    return 0


def _kill_tree(pid: int) -> None:
    """Terminate a component and its children.

    The venv's python.exe is a redirector that runs the real interpreter as a
    child process, so killing only the pid we recorded would leave the actual
    server orphaned and still holding the port.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
        return
    try:  # pragma: no cover - posix
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)


def cmd_stop() -> int:
    state = _load_state()
    if not state:
        print("Nothing recorded in data/logs/stack.json — nothing to stop.")
        return 0
    survivors: dict = {}
    for name, entry in state.items():
        pid = entry["pid"]
        if not _alive(pid):
            print(f"  {name} (pid {pid}) was not running")
            continue
        recorded = entry.get("identity")
        if recorded is None:
            # Written before identities were recorded. It may well be ours, so
            # the record is kept rather than dropped -- but it is not killed,
            # because an unverified pid is exactly what this guard exists for.
            print(f"  {name} (pid {pid}) predates identity checks, so it "
                  "cannot be confirmed as ours -- not killing it")
            print("     stop it by hand, then delete data/logs/stack.json")
            survivors[name] = entry
            continue
        if recorded != _process_identity(pid):
            # Definitively someone else: taskkill /T here would take out that
            # process AND all its children, and report "stopped collector".
            print(f"  {name} (pid {pid}) is NOT the process this script "
                  "started -- the pid was reused; ignoring it")
            continue
        _kill_tree(pid)
        deadline = time.time() + 10.0
        while time.time() < deadline and _alive(pid):
            time.sleep(0.2)
        if _alive(pid):
            print(f"  FAILED to stop {name} (pid {pid})")
            survivors[name] = entry
        else:
            print(f"  stopped {name} (pid {pid})")
    if survivors:
        # Keep what is still running. Dropping the file unconditionally lost
        # the pid of a component that had just refused to die, after which the
        # next start saw no collector recorded and launched a second one.
        _save_state(survivors)
        return 1
    STATE.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start/stop the read-only odb_scanner stack")
    parser.add_argument("--stop", action="store_true",
                        help="stop the components started by this script")
    parser.add_argument("--status", action="store_true",
                        help="print what is running")
    parser.add_argument("--no-web", action="store_true",
                        help="do not start the dashboard")
    parser.add_argument("--cycles", type=int, default=None,
                        help="collector cycles (default: run continuously)")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    if args.stop:
        return cmd_stop()
    cfg = load_config(args.config)
    if args.status:
        return cmd_status(cfg)
    return cmd_start(cfg, args.cycles, not args.no_web)


if __name__ == "__main__":
    raise SystemExit(main())
