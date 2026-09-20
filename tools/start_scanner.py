"""Start / stop / inspect the odb_scanner stack (simulator + collector + web).

The three components are independent processes (see README "Manual
equivalent, in two terminals"): this script just starts them detached with
logs in data/logs/ and a pid file, so it can be re-run safely — anything that
is already running is skipped instead of duplicated.

Usage:
    python tools/start_scanner.py                 # start simulator + collector + dashboard
    python tools/start_scanner.py --cycles 1      # one collection pass, then stop collecting
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


def _spawn(name: str, args: list[str]) -> int:
    """Start a component detached so it survives this script exiting."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    log = (LOG_DIR / f"{name}.log").open("ab")
    try:
        proc = subprocess.Popen(
            [_python(), *args], cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    finally:
        log.close()
    return proc.pid


def _load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return {k: int(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


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
        if cfg.get("adapter.type", "elm327_tcp") == "elm327_tcp":
            if (state.get("simulator") and _alive(state["simulator"])) \
                    or _port_open(sim_host, sim_port):
                skipped.append(f"simulator already up on {sim_host}:{sim_port}")
            else:
                pid = _spawn("simulator", ["main.py", "simulate"])
                state["simulator"] = pid
                ok = _wait_port(sim_host, sim_port)
                started.append(
                    f"simulator pid {pid} "
                    + (f"listening on {sim_host}:{sim_port}" if ok else
                       f"FAILED to listen on {sim_host}:{sim_port} "
                       "(see data/logs/simulator.log)"))
        else:
            skipped.append("simulator not needed "
                           f"(adapter.type = {cfg.get('adapter.type')})")

        if (state.get("collector") and _alive(state["collector"])):
            skipped.append(f"collector already running (pid {state['collector']})")
        else:
            args = ["main.py", "collect"]
            if cycles is not None:
                args += ["--cycles", str(cycles)]
            pid = _spawn("collector", args)
            state["collector"] = pid
            started.append(f"collector pid {pid} -> {cfg.get('database.path')} "
                           f"(log: data/logs/collector.log)")

        web_host = cfg.get("web.host", "127.0.0.1")
        web_port = int(cfg.get("web.port", 8000))
        if not with_web:
            skipped.append("web dashboard disabled (--no-web)")
        elif (state.get("web") and _alive(state["web"])) \
                or _port_open(web_host, web_port):
            skipped.append(f"web dashboard already up on http://{web_host}:{web_port}")
        else:
            pid = _spawn("web", ["main.py", "serve"])
            state["web"] = pid
            ok = _wait_port(web_host, web_port)
            started.append(
                f"web pid {pid} "
                + (f"-> http://{web_host}:{web_port}" if ok else
                   f"FAILED to listen on {web_host}:{web_port} "
                   "(see data/logs/web.log)"))
    finally:
        LOCK.unlink(missing_ok=True)

    _save_state(state)
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
    web_host = cfg.get("web.host", "127.0.0.1")
    web_port = int(cfg.get("web.port", 8000))
    rows = [
        ("simulator", state.get("simulator"),
         f"{sim_host}:{sim_port} listening={_port_open(sim_host, sim_port)}"),
        ("collector", state.get("collector"), f"db={cfg.get('database.path')}"),
        ("web", state.get("web"),
         f"{web_host}:{web_port} listening={_port_open(web_host, web_port)}"),
    ]
    for name, pid, extra in rows:
        if pid is None:
            print(f"  {name:<10} not started by this script   {extra}")
        else:
            print(f"  {name:<10} pid {pid:<6} "
                  f"alive={_alive(pid)}   {extra}")
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
    for name, pid in state.items():
        if not _alive(pid):
            print(f"  {name} (pid {pid}) was not running")
            continue
        _kill_tree(pid)
        deadline = time.time() + 10.0
        while time.time() < deadline and _alive(pid):
            time.sleep(0.2)
        print(f"  {'stopped' if not _alive(pid) else 'FAILED to stop'} "
              f"{name} (pid {pid})")
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
