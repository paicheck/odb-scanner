"""End-to-end check of the local cloud stack (no hosted infrastructure needed).

Proves the whole cloud path against a real database and the real ingest API,
using a scratch SQLite database so nothing in data/real_car.db is touched:

    scratch SQLite -> Repository -> cloud client -> HTTP -> ingest API
      -> MariaDB -> verified by reading the API's own /status counts

Start the stack first, run this, then tear it down. Either stack works --
pick one:

    # Docker (or on Linux/macOS)
    docker compose -f cloud/docker-compose.yml up -d --build

    # No Docker, no admin rights (Windows)
    python tools/local_cloud.py up

    python tools/cloud_local_check.py
    docker compose -f cloud/docker-compose.yml down -v   # or: local_cloud.py down

The rows it writes are deliberately obvious test data -- the VIN is not a real
one and the source is "python:cloud-check" -- so they are trivial to delete if
this is ever pointed at a database that also holds real readings.

Exit code 0 = every check passed, 1 = a check failed, 2 = the stack is not up.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud.client import CloudClient, sync_pending  # noqa: E402
from cloud.config import ClientSettings  # noqa: E402
from database.repository import Repository, utcnow  # noqa: E402
from web.auth import is_loopback  # noqa: E402

# Not a real VIN: makes any leftover check rows unmistakable.
TEST_VIN = "TESTCLOUD00000000"
TEST_SOURCE = "python:cloud-check"

_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> bool:
    """Record one check. ok=None means SKIP."""
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    _results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    return bool(ok)


def target_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def settings_for(url: str, token: str, source_id: str) -> ClientSettings:
    return ClientSettings(
        enabled=True, api_url=url, token=token, source_id=source_id,
        batch_size=500, sync_interval_s=1.0, timeout_s=10.0, verify_tls=False,
    )


def status_counts(url: str, token: str) -> dict:
    response = requests.get(f"{url.rstrip('/')}/status",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=10)
    response.raise_for_status()
    return response.json()


def seed_scratch_database(path: Path, count: int) -> None:
    """A handful of measurements, shaped exactly like collected ones."""
    with Repository(path) as repo:
        vid = repo.ensure_vehicle(TEST_VIN, make="TEST", model="CLOUD-CHECK")
        for i in range(count):
            repo.record_measurement(
                vid, utcnow(), f"cloud_check_{i}", "bms", "UDS-0x22", "0x2AB8",
                "kWh", "reported", "scalar", "verified",
                raw_response=f"62 2AB8 0{i}", value=30.0 + i,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080",
                        help="ingest API base URL (default: local compose stack)")
    parser.add_argument("--token", default="local-dev-token",
                        help="must match API_TOKEN in cloud/.env or the "
                             "compose default")
    parser.add_argument("--measurements", type=int, default=5,
                        help="test rows to upload (default 5)")
    parser.add_argument("--allow-remote", action="store_true",
                        help="permit a non-loopback URL; required, because the "
                             "check WRITES test rows wherever it points")
    args = parser.parse_args(argv)

    host = target_host(args.url)
    if not is_loopback(host) and not args.allow_remote:
        # Guard rail: this script writes data. Pointed at a real deployment by
        # accident, it would add fake readings to the vehicle's archive.
        print(f"[FAIL] {args.url} is not a loopback address. This check writes "
              f"test rows; re-run with --allow-remote only if that is intended.")
        return 1

    try:
        before = status_counts(args.url, args.token)
    except requests.RequestException as exc:
        print(f"[FAIL] ingest API not reachable at {args.url}: {exc}\n"
              "Start the local stack first:\n"
              "  docker compose -f cloud/docker-compose.yml up -d --build\n"
              "  python tools/local_cloud.py up        (no Docker / no admin rights)")
        return 2
    check("ingest API reachable", True, f"{args.url} ({before} rows stored)")

    settings = settings_for(args.url, args.token, TEST_SOURCE)
    client = CloudClient(settings)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cloud_check.db"
            seed_scratch_database(db, args.measurements)
            check("scratch data seeded", True, f"{args.measurements} rows")

            with Repository(db) as repo:
                result = sync_pending(repo, client, settings)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        check("upload through the ingest API", False, f"{type(exc).__name__}: {exc}")
        return _summary()
    finally:
        client.close()

    accounted = result["uploaded"] + result["duplicates"]
    check("every row acknowledged",
          accounted == args.measurements,
          f"{result['uploaded']} new, {result['duplicates']} already present")

    # Read the counts back from the API: the rows have to be visible in MySQL,
    # not merely accepted by the HTTP layer. A re-run legitimately inserts
    # nothing new (the ids are deterministic, so they deduplicate), which is why
    # the expectation is "before + newly inserted", not "before + sent".
    after = status_counts(args.url, args.token)
    expected = before["measurements"] + result["uploaded"]
    check("rows visible in the database",
          after["measurements"] == expected,
          f"{before['measurements']} -> {after['measurements']} "
          f"(expected {expected})")
    check("source registered", after["sources"] >= 1,
          f"{after['sources']} known source(s)")
    return _summary()


def _summary() -> int:
    failed = [r for r in _results if r[1] == "FAIL"]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed.")
    if failed:
        print("Cloud logging is NOT working end to end. See the FAIL lines above.")
        return 1
    print("Cloud logging works end to end: collector -> API -> MySQL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
