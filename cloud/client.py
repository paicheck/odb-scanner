"""Push locally-collected measurements to the cloud ingest API.

The local SQLite database stays the source of truth. This module only ever
READS from it (plus a watermark row in the existing `meta` table) and POSTs
batches; if the cloud is unreachable the collector carries on completely
unaffected and the next run resumes from exactly where it stopped.

WHY A WATERMARK, NOT AN "uploaded" FLAG
---------------------------------------
`meta` already exists and needs no schema change, and a single monotonic row id
survives restarts far more simply than a column on every measurement. The
watermark is advanced ONLY after the API has accepted the batch, so a crash or
a dropped response re-sends rather than loses. Re-sending is safe because cloud
rows are immutable and the API ignores an id it already holds -- which is why
the cloud id is derived deterministically (see mapping.cloud_measurement_id)
rather than generated fresh per attempt.
"""
from __future__ import annotations

import logging
import socket
import threading

import requests

from .config import ClientSettings
from .mapping import cloud_measurement_id

log = logging.getLogger(__name__)

# Key under which the highest-successfully-uploaded measurement id is stored in
# the `meta` table. Read with a leading probe every sync, so it is the one
# piece of cloud state that lives on this machine.
WATERMARK_KEY = "cloud_sync.measurements.last_id"

_PENDING_SQL = """
SELECT m.id, v.vin, m.ts, m.ecu, m.service, m.pid, m.key, m.value, m.text_value,
       m.unit, m.provenance, m.decoding_method, m.doc_status, m.raw_response,
       m.success, m.error
FROM measurements m
JOIN vehicles v ON v.id = m.vehicle_id
WHERE m.id > ?
ORDER BY m.id
LIMIT ?
"""


class CloudSyncError(RuntimeError):
    """The cloud could not be reached or refused a batch. Always retryable."""


# -- watermark ---------------------------------------------------------------
def get_watermark(repo) -> int:
    """Highest local measurement id already accepted by the cloud (0 if none)."""
    row = repo.conn.execute(
        "SELECT value FROM meta WHERE key = ?", (WATERMARK_KEY,)).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        # A hand-edited or truncated value must not wedge the uploader: falling
        # back to 0 re-sends everything, which is harmless because the cloud
        # deduplicates by id.
        log.warning("%s in meta is not an integer (%r); restarting from 0",
                    WATERMARK_KEY, row["value"])
        return 0


def set_watermark(repo, value: int) -> None:
    repo.conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (WATERMARK_KEY, str(int(value))),
    )
    repo.conn.commit()


# -- payload -----------------------------------------------------------------
def pending_rows(repo, after_id: int, limit: int) -> list[dict]:
    return [dict(row) for row in repo.conn.execute(_PENDING_SQL, (after_id, limit))]


def _row_payload(source_id: str, row: dict) -> dict:
    return {
        "id": cloud_measurement_id(source_id, row["id"]),
        "vin": row["vin"],
        "ts": row["ts"],
        "ecu": row["ecu"],
        "service": row["service"],
        "pid": row["pid"],
        "key": row["key"],
        "value": row["value"],
        "text_value": row["text_value"],
        "unit": row["unit"],
        "provenance": row["provenance"],
        "decoding_method": row["decoding_method"],
        "doc_status": row["doc_status"],
        "raw_response": row["raw_response"],
        "success": bool(row["success"]),
        "error": row["error"],
    }


def build_payload(source_id: str, rows: list[dict],
                  label: str | None = None) -> dict:
    return {
        "source": {
            "source_id": source_id,
            "kind": "python",
            "label": label or socket.gethostname(),
        },
        "measurements": [_row_payload(source_id, row) for row in rows],
    }


# -- HTTP --------------------------------------------------------------------
class CloudClient:
    """Batch uploader. One instance reuses a connection pool across batches."""

    def __init__(self, settings: ClientSettings):
        self.settings = settings
        self._session = requests.Session()

    def upload(self, payload: dict) -> dict:
        try:
            response = self._session.post(
                self.settings.upload_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.token}"},
                timeout=self.settings.timeout_s,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            # Deliberately does not include the URL's query string or the token
            # in the message: this string ends up in a log file.
            raise CloudSyncError(f"could not reach the ingest API: {exc}") from exc
        if response.status_code >= 400:
            raise CloudSyncError(
                f"ingest API returned HTTP {response.status_code}: "
                f"{response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise CloudSyncError("ingest API returned a non-JSON response") from exc

    def close(self) -> None:
        self._session.close()


# -- the sync itself ---------------------------------------------------------
def sync_pending(repo, client: CloudClient, settings: ClientSettings,
                 max_batches: int | None = None) -> dict:
    """Upload pending batches until there is nothing left (or max_batches).

    The watermark is re-read at the top of every batch so rows the collector
    wrote while this was uploading are picked up in the same run rather than
    waiting for the next one.
    """
    batches = uploaded = duplicates = 0
    while True:
        rows = pending_rows(repo, get_watermark(repo), settings.batch_size)
        if not rows:
            break

        result = client.upload(build_payload(settings.source_id, rows))
        # Only now is it safe to move the watermark: the API has durably stored
        # these rows, so the next run starts after the last id in this batch.
        set_watermark(repo, max(row["id"] for row in rows))

        batches += 1
        uploaded += int(result.get("accepted", 0))
        duplicates += int(result.get("duplicates", 0))

        if len(rows) < settings.batch_size:
            break
        if max_batches is not None and batches >= max_batches:
            break

    return {"batches": batches, "uploaded": uploaded, "duplicates": duplicates}


class CloudSyncService:
    """Periodic background uploader for a running collector.

    Runs on its own daemon thread and opens its own SQLite connection (the
    Repository hands one out per thread), so it never contends with the
    collector's connection or blocks a vehicle read. Any cloud failure is
    logged and retried on the next tick -- collection is never affected.
    """

    def __init__(self, settings: ClientSettings, repo, client: CloudClient | None = None):
        self.settings = settings
        self.repo = repo
        self.client = client or CloudClient(settings)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict:
        return sync_pending(self.repo, self.client, self.settings)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="cloud-sync",
                                        daemon=True)
        self._thread.start()
        log.info("cloud sync started: source=%s every %.0fs to %s",
                 self.settings.source_id, self.settings.sync_interval_s,
                 self.settings.api_url)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.run_once()
                if result["batches"]:
                    log.info("cloud sync: %d row(s) uploaded, %d duplicate(s) "
                             "ignored over %d batch(es)",
                             result["uploaded"], result["duplicates"],
                             result["batches"])
            except CloudSyncError as exc:
                # Expected whenever the network is down. One warning, then carry
                # on: the watermark has not moved, so nothing is lost.
                log.warning("cloud sync deferred, will retry in %.0fs: %s",
                            self.settings.sync_interval_s, exc)
            except Exception:
                log.exception("cloud sync failed unexpectedly; retrying in %.0fs",
                              self.settings.sync_interval_s)
            self._stop.wait(self.settings.sync_interval_s)

    def stop(self, timeout: float | None = None) -> None:
        """Stop the uploader and close its session.

        The join waits longer than one HTTP request can take by default, so the
        session is not pulled out from under an in-flight upload. If the wait
        still expires, the thread is left to finish on its own rather than
        closing the client underneath it -- the process is exiting anyway.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(self.settings.timeout_s + 5.0 if timeout is None else timeout)
            if thread.is_alive():
                log.warning("cloud sync thread did not stop within the timeout; "
                            "leaving it to finish")
                return
        self.client.close()
