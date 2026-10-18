"""Local MariaDB + ingest API, with no Docker and no admin rights.

Docker Desktop needs administrator rights, WSL2 and a reboot; this gets the same
thing done unprivileged, in a user directory, in a couple of minutes. It runs a
portable MariaDB (the official Windows ZIP) plus `cloud.api` with uvicorn, which
is exactly the pair the compose stack provides, so `tools/cloud_local_check.py`
validates the real code path either way.

Usage:
    python tools/local_cloud.py install     download + initialise MariaDB (once)
    python tools/local_cloud.py up          start MariaDB and the ingest API
    python tools/local_cloud.py status      what is running
    python tools/local_cloud.py down        stop both
    python tools/local_cloud.py sql "SELECT COUNT(*) FROM measurements;"

Then:
    python tools/cloud_local_check.py

Everything lives OUTSIDE the repository, under
`%LOCALAPPDATA%\\odb_scanner` (override with ODB_LOCAL_CLOUD_DIR). That is
deliberate: the repository lives in a OneDrive folder, and a 300 MB database
plus its WAL files would be synced continuously and hold locks on files that
change every second.

The port is 3307, not 3306, so this cannot collide with a real MySQL already
installed on the machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Reused rather than reimplemented: pid identity checking and tree-killing on
# Windows are subtle enough that a second copy would eventually diverge, and a
# wrong kill takes out whatever process reused the pid.
from tools.start_scanner import (  # noqa: E402
    _alive,
    _is_ours,
    _kill_tree,
    _port_open,
    _process_identity,
    _wait_port,
)

# -- pinned artefact ----------------------------------------------------------
# Both the URL and the digest are pinned so the download cannot silently change
# underneath us: MariaDB publishes the SHA-256 next to every file, and a
# mismatch aborts before anything is extracted or run.
MARIADB_VERSION = "11.8.9"
ZIP_NAME = f"mariadb-{MARIADB_VERSION}-winx64.zip"
ZIP_URL = (f"https://downloads.mariadb.org/rest-api/mariadb/{MARIADB_VERSION}/"
           f"{ZIP_NAME}")
ZIP_SHA256 = "830c46727d9278eae212ae3eca44eeb9e71b2a68704e95f344a64fba7b1963f5"

# -- local instance settings (development values, obvious on purpose) ---------
DB_PORT = 3307
DB_NAME = "odb_cloud"
DB_USER = "odb_ingest"
DB_PASSWORD = "ingest-local-only"
ROOT_PASSWORD = "root-local-only"
API_PORT = 8080
API_TOKEN = "local-dev-token"

# Long enough for a cold mysqld to replay its InnoDB log on a slow disk.
DB_START_TIMEOUT_S = 60.0
API_START_TIMEOUT_S = 30.0


def base_dir() -> Path:
    override = os.environ.get("ODB_LOCAL_CLOUD_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "odb_scanner"
    return Path.home() / ".local" / "share" / "odb_scanner"


BASE = base_dir()
BIN = BASE / "mariadb" / "bin"
DATADIR = BASE / "data"
DOWNLOADS = BASE / "downloads"
LOGS = BASE / "logs"
STATE = BASE / "local_cloud.json"

MYSQLD = BIN / "mysqld.exe"
MARIADB = BIN / "mariadb.exe"
INSTALL_DB = BIN / "mariadb-install-db.exe"

if os.name != "nt":
    # The pinned artefact is a Windows ZIP. Fail clearly rather than halfway
    # through a download that cannot be extracted into something runnable.
    MYSQLD = BIN / "mysqld"


# --- state (name -> pid + identity) ------------------------------------------
def load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    out = {}
    for name, value in (data or {}).items():
        if isinstance(value, dict):
            try:
                out[name] = {"pid": int(value["pid"]),
                             "identity": value.get("identity")}
            except (KeyError, TypeError, ValueError):
                continue
    return out


def save_state(state: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def record(pid: int) -> dict:
    return {"pid": pid, "identity": _process_identity(pid)}


def spawn(name: str, argv: list[str], env_extra: dict | None = None) -> int:
    """Start a process detached, its output in BASE/logs/<name>.out."""
    LOGS.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    log = (LOGS / f"{name}.out").open("ab")
    try:
        proc = subprocess.Popen(
            argv, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, creationflags=flags,
            env={**os.environ, **(env_extra or {})})
    finally:
        log.close()
    return proc.pid


# --- install -----------------------------------------------------------------
def _download_zip(zip_path: Path) -> None:
    if zip_path.exists():
        if _sha256(zip_path) == ZIP_SHA256:
            print(f"  download already present and verified: {zip_path.name}")
            return
        print("  existing download does not match the published digest; "
              "downloading again")
        zip_path.unlink()

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {ZIP_NAME} (~92 MB) ...")
    tmp = zip_path.with_suffix(".part")
    digest = hashlib.sha256()
    with urllib.request.urlopen(ZIP_URL, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last_report = 0.0
        with open(tmp, "wb") as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                # Progress at most twice a second: printing per megabyte makes
                # the download slower than the network is.
                if time.time() - last_report > 0.5:
                    pct = f"{done * 100 // total}%" if total else f"{done >> 20} MB"
                    print(f"    {pct}", flush=True)
                    last_report = time.time()
    if digest.hexdigest() != ZIP_SHA256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"SHA-256 mismatch downloading {ZIP_NAME}: refusing to extract. "
            "Expected " + ZIP_SHA256)
    os.replace(tmp, zip_path)
    print("  download verified against the published SHA-256")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract(zip_path: Path, target: Path) -> None:
    print("  extracting ...")
    staging = BASE / "_extract"
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)
    # The archive has a single mariadb-<version>-winx64/ root; strip it so the
    # tools land at <target>/bin/... and paths stay predictable.
    roots = [p for p in staging.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"unexpected archive layout in {zip_path.name}")
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(roots[0]), str(target))
    shutil.rmtree(staging, ignore_errors=True)


def cmd_install(_args) -> int:
    print(f"Local MariaDB into {BASE}")
    if os.name != "nt":
        print("  This helper installs the Windows build; on POSIX use Docker "
              "or your package manager's mariadb server instead.")
        return 1

    if MYSQLD.exists():
        print("  MariaDB already extracted")
    else:
        zip_path = DOWNLOADS / ZIP_NAME
        _download_zip(zip_path)
        _extract(zip_path, BASE / "mariadb")

    if (DATADIR / "mysql").exists():
        print("  data directory already initialised")
    else:
        DATADIR.parent.mkdir(parents=True, exist_ok=True)
        print("  initialising the data directory ...")
        result = subprocess.run(
            [str(INSTALL_DB), f"--datadir={DATADIR}", f"--password={ROOT_PASSWORD}",
             f"--port={DB_PORT}"],
            capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            return 1
        print("  data directory ready")

    print("\nInstalled. Next: python tools/local_cloud.py up")
    return 0


# --- up / down ---------------------------------------------------------------
def _db_running(state: dict) -> bool:
    return _is_ours(state.get("mariadb")) or _port_open("127.0.0.1", DB_PORT)


def _ensure_database() -> bool:
    """Create the application database and user. Idempotent."""
    sql = (
        f"CREATE DATABASE IF NOT EXISTS {DB_NAME} "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS '{DB_USER}'@'%' IDENTIFIED BY '{DB_PASSWORD}';\n"
        f"GRANT ALL PRIVILEGES ON {DB_NAME}.* TO '{DB_USER}'@'%';\n"
        "FLUSH PRIVILEGES;\n"
    )
    result = subprocess.run(
        [str(MARIADB), "--host=127.0.0.1", f"--port={DB_PORT}", "--user=root",
         f"--password={ROOT_PASSWORD}"],
        input=sql, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        return False
    return True


def _missing_api_deps(names: tuple[str, ...] = ("pymysql", "fastapi",
                                                "uvicorn")) -> list[str]:
    """API modules this interpreter cannot import.

    Checked before anything is spawned: otherwise uvicorn exits immediately and
    `up` reports only "the API failed to listen", with the real cause (a missing
    database driver) buried in a log file.
    """
    missing = []
    for name in names:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return missing


def cmd_up(_args) -> int:
    if not MYSQLD.exists():
        print("MariaDB is not installed yet. Run: "
              "python tools/local_cloud.py install")
        return 1

    # Before starting the database, so a missing driver does not leave the
    # stack half-up (MariaDB running, no API).
    missing = _missing_api_deps()
    if missing:
        print("The ingest API needs packages this Python cannot import: "
              + ", ".join(missing))
        print("Install them with:  "
              "python -m pip install -r cloud/requirements.txt")
        return 1

    state = load_state()
    started, skipped = [], []

    if _db_running(state):
        skipped.append(f"MariaDB already up on 127.0.0.1:{DB_PORT}")
    else:
        pid = spawn("mariadb", [str(MYSQLD), f"--datadir={DATADIR}",
                                f"--port={DB_PORT}", "--bind-address=127.0.0.1",
                                "--console"])
        state["mariadb"] = record(pid)
        save_state(state)
        if _wait_port("127.0.0.1", DB_PORT, DB_START_TIMEOUT_S):
            started.append(f"MariaDB pid {pid} on 127.0.0.1:{DB_PORT}")
        else:
            started.append(f"MariaDB pid {pid} FAILED to listen "
                           f"(see {LOGS / 'mariadb.out'})")

    # Wait again rather than only in the branch above: a mysqld someone else
    # started (or a slow first start) still has to be reachable before the
    # database and user can be created.
    if _wait_port("127.0.0.1", DB_PORT, DB_START_TIMEOUT_S):
        if _ensure_database():
            skipped.append(f"database {DB_NAME} and user {DB_USER} present")
        else:
            started.append("FAILED to create the application database/user")

    if _is_ours(state.get("api")) or _port_open("127.0.0.1", API_PORT):
        skipped.append(f"ingest API already up on 127.0.0.1:{API_PORT}")
    else:
        pid = spawn("api", [sys.executable, "-m", "uvicorn",
                            "cloud.api:create_app", "--factory",
                            "--host", "127.0.0.1", "--port", str(API_PORT),
                            "--log-level", "warning"],
                    env_extra={
                        "ODB_CLOUD_API_TOKEN": API_TOKEN,
                        "ODB_CLOUD_DB_HOST": "127.0.0.1",
                        "ODB_CLOUD_DB_PORT": str(DB_PORT),
                        "ODB_CLOUD_DB_USER": DB_USER,
                        "ODB_CLOUD_DB_PASSWORD": DB_PASSWORD,
                        "ODB_CLOUD_DB_NAME": DB_NAME,
                    })
        state["api"] = record(pid)
        save_state(state)
        if _wait_port("127.0.0.1", API_PORT, API_START_TIMEOUT_S):
            started.append(f"ingest API pid {pid} on http://127.0.0.1:{API_PORT}")
        else:
            started.append(f"ingest API pid {pid} FAILED to listen "
                           f"(see {LOGS / 'api.out'})")

    for line in started:
        print(f"  started: {line}")
    for line in skipped:
        print(f"  skipped: {line}")

    print("\nCheck it end to end:  python tools/cloud_local_check.py")
    print(f"API token:            {API_TOKEN}")
    print(f"Database:             {DB_NAME} on 127.0.0.1:{DB_PORT} "
          f"as {DB_USER}")
    print(f"Logs:                 {LOGS}")
    return 1 if any("FAILED" in line for line in started) else 0


def cmd_down(_args) -> int:
    state = load_state()
    if not state:
        print(f"Nothing recorded in {STATE} - nothing to stop.")
        return 0
    survivors = {}
    for name in ("api", "mariadb"):
        entry = state.get(name)
        if entry is None:
            continue
        pid = entry["pid"]
        if not _alive(pid):
            print(f"  {name} (pid {pid}) was not running")
            continue
        if not _is_ours(entry):
            print(f"  {name} (pid {pid}) is NOT the process this script "
                  "started (pid reused) - not killing it")
            continue
        _kill_tree(pid)
        deadline = time.time() + 20.0
        while time.time() < deadline and _alive(pid):
            time.sleep(0.2)
        if _alive(pid):
            print(f"  FAILED to stop {name} (pid {pid})")
            survivors[name] = entry
        else:
            print(f"  stopped {name} (pid {pid})")
    if survivors:
        save_state(survivors)
        return 1
    STATE.unlink(missing_ok=True)
    return 0


def cmd_status(_args) -> int:
    state = load_state()
    db_up = _port_open("127.0.0.1", DB_PORT)
    api_up = _port_open("127.0.0.1", API_PORT)
    for name, entry, detail in (
        ("mariadb", state.get("mariadb"), f"127.0.0.1:{DB_PORT} listening={db_up}"),
        ("api", state.get("api"),
         f"http://127.0.0.1:{API_PORT} listening={api_up}"),
    ):
        if entry is None:
            print(f"  {name:<8} not started by this script   {detail}")
            continue
        pid = entry["pid"]
        if _is_ours(entry):
            state_text = "alive=True"
        elif _alive(pid):
            state_text = "alive=True (DIFFERENT process - pid reused)"
        else:
            state_text = "alive=False"
        print(f"  {name:<8} pid {pid:<6} {state_text}   {detail}")
    print(f"  install: {BASE}  ({'present' if MYSQLD.exists() else 'MISSING'})")
    return 0


def cmd_sql(args) -> int:
    result = subprocess.run(
        [str(MARIADB), "--host=127.0.0.1", f"--port={DB_PORT}",
         f"--user={DB_USER}", f"--password={DB_PASSWORD}",
         f"--database={DB_NAME}", "--table", "-e", args.query],
        capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local (Docker-free) MariaDB + cloud ingest API")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="download and initialise MariaDB")
    sub.add_parser("up", help="start MariaDB and the ingest API")
    sub.add_parser("down", help="stop both")
    sub.add_parser("status", help="show what is running")
    sql = sub.add_parser("sql", help="run a query against the local database")
    sql.add_argument("query")

    args = parser.parse_args()
    return {
        "install": cmd_install,
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "sql": cmd_sql,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
