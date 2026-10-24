"""Build a normalised SQL observation database from the collected OBD data.

WHY THIS TOOL EXISTS
--------------------
`data/odb_scanner.db` is the collector's live store and is shaped for writing,
not for asking questions: `measurements` is one wide table with a free-text
signal key, provenance and doc status repeated on every row, while the useful
evidence archives (`live_*.json`, `gateway_*.json`, `passive_*.json`,
`strict_expected_*.json`) are unqueryable JSON.

This tool builds a second database, `data/observations.db` by default:

  * dimensions (`vehicle`, `module`, `signal`, `data_source`) separated from
    facts (`observation`, `cell_voltage`, `battery_snapshot`, ...);
  * foreign keys, checks and indexes -- not just a dump of the old table;
  * the JSON capture archives loaded into `capture` / `capture_record`; and
  * views that answer the common questions directly.

It is intentionally non-destructive:

  * the source database is opened read-only and is hashed before and after;
  * the output is written to a temporary file and only then replaced;
  * an existing output file is refused unless `--force` is given.

Usage:
    python tools/build_observations_db.py
    python tools/build_observations_db.py --source data/real_car.db
    python tools/build_observations_db.py --out data/obs.db --force
    python tools/build_observations_db.py --no-captures
    python tools/build_observations_db.py --captures path/to/capture.json
    python tools/build_observations_db.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from decoders.catalogue import CATALOGUE_VERSION, build_catalogue  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

SCHEMA_PATH = ROOT / "database" / "observations_schema.sql"
SCHEMA_VERSION = 1
DEFAULT_DB = ROOT / "data" / "observations.db"

# Archives that are part of the evidence package. `data/logs/gateway_scan_*`
# is deliberately absent: it is byte-identical to the archived copy in
# `captures/`, and loading both would double-count the same scan.
DEFAULT_CAPTURE_GLOBS = (
    "research/measurement_discovery/captures/*.json",
    "data/logs/climate_*.json",
)

# Nested fields used by the `watch_27c4` timer capture.
WATCH_FIELD_KEYS = {
    "windshield": "windshield_temperature",
    "outside": "outside_temp",
    "inside": "inside_temp",
    "front_left_deflector": "front_left_deflector_temperature",
}

IMPORTED_PREFIX = "imported"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_only_uri(path: Path) -> str:
    """A URI that SQLite will only open for reading, even on Windows paths."""
    posix = path.resolve().as_posix()
    return f"file:{quote(posix, safe='/:')}?mode=ro"


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    # Source `service` values look like "UDS-0x22" and "OBD-II-0x01", not bare
    # numbers. Pull the hex token out rather than raising on the prefix.
    match = re.search(r"0[xX]([0-9A-Fa-f]+)", text)
    if match:
        return int(match.group(1), 16)
    try:
        return int(text)
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _hex_or_none(raw: Any) -> str | None:
    """Return normalised uppercase hex, or None when the payload is not bytes."""
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "")
    if not text or len(text) % 2:
        return None
    try:
        bytes.fromhex(text)
    except ValueError:
        return None
    return text.upper()


def _provenance_kind(provenance: str) -> str:
    if provenance.startswith(IMPORTED_PREFIX):
        return "imported"
    if provenance in {"reported", "calculated", "estimated", "simulated"}:
        return provenance
    return "unknown"


def _iso_in_text(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
                      r"(?:Z|[+-]\d{2}:\d{2})", str(text))
    return match.group(0) if match else None


def _capture_kind(path: Path) -> str:
    name = path.name.lower()
    if "validation" in name:
        return "validation_report"
    if "manifest" in name:
        return "manifest"
    if "passive" in name:
        return "passive_capture"
    if "strict_expected" in name:
        return "expected_values"
    if "gateway" in name:
        return "gateway_scan"
    if name.startswith("climate_") or name.startswith("watch_"):
        return "timed_watch"
    return "live_capture"


def _is_manifest(path: Path) -> bool:
    return path.name.lower().endswith(".manifest.json")


def _is_validation(path: Path) -> bool:
    return "validation" in path.name.lower()


def _capture_stem(path: Path) -> str:
    """`live_20261021_validation_strict.json` -> `live_20261021`."""
    stem = path.name
    if stem.endswith(".json"):
        stem = stem[: -len(".json")]
    if stem.endswith(".manifest"):
        stem = stem[: -len(".manifest")]
    for suffix in ("_validation_strict", "_validation"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def default_capture_paths(root: Path = ROOT) -> list[Path]:
    found: list[Path] = []
    for pattern in DEFAULT_CAPTURE_GLOBS:
        found.extend(sorted(root.glob(pattern)))
    return found


def resolve_captures(explicit: Iterable[str] | None, root: Path = ROOT) -> list[Path]:
    """Resolve --captures (files or directories) into a de-duplicated list."""
    if explicit is None:
        return [p for p in default_capture_paths(root) if p.is_file()]
    paths: list[Path] = []
    seen: set[Path] = set()
    for item in explicit:
        path = Path(item)
        if not path.is_absolute():
            path = root / path
        candidates = sorted(path.glob("*.json")) if path.is_dir() else [path]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            paths.append(resolved)
    return paths


def _parse_capture_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: cannot read capture JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class _Builder:
    def __init__(self, source: Path, out: Path, profile: str):
        self.source = source
        self.out = out
        self.profile = profile
        self.con: sqlite3.Connection | None = None
        self.src: sqlite3.Connection | None = None
        self.signals: dict[str, int] = {}
        self.sources: dict[str, int] = {}
        self.modules: dict[tuple[int, str], int] = {}
        self.definitions: dict[str, Any] = {}
        self.summary: dict[str, Any] = {}

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> None:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out.with_name(self.out.name + ".tmp")
        for suffix in ("", "-wal", "-shm"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)
        self.tmp = tmp
        self.con = sqlite3.connect(str(tmp))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute("PRAGMA journal_mode = DELETE")
        self.con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.src = sqlite3.connect(read_only_uri(self.source), uri=True)
        self.src.row_factory = sqlite3.Row

    def close(self) -> None:
        if self.src is not None:
            self.src.close()
        if self.con is not None:
            self.con.close()

    # -- dimensions ---------------------------------------------------------
    def load_vehicles(self) -> int:
        assert self.src is not None and self.con is not None
        if not _table_exists(self.src, "vehicles"):
            return 0
        rows = self.src.execute(
            "SELECT id, vin, make, model, variant, year, first_seen "
            "FROM vehicles ORDER BY id"
        ).fetchall()
        for r in rows:
            self.con.execute(
                "INSERT INTO vehicle(vehicle_id, vin, make, model, variant, year, "
                "first_seen, source_db) VALUES (?,?,?,?,?,?,?,?)",
                (r["id"], r["vin"], r["make"], r["model"], r["variant"],
                 r["year"], r["first_seen"], self.source.name),
            )
        return len(rows)

    def load_modules(self) -> int:
        assert self.src is not None and self.con is not None
        if not _table_exists(self.src, "ecus"):
            return 0
        rows = self.src.execute(
            "SELECT id, vehicle_id, key, name, tx_id, rx_id, status, doc_status, "
            "first_seen, last_seen FROM ecus ORDER BY id"
        ).fetchall()
        for r in rows:
            self.con.execute(
                "INSERT INTO module(module_id, vehicle_id, key, name, tx_id, rx_id, "
                "status, doc_status, first_seen, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r["vehicle_id"], r["key"], r["name"], r["tx_id"],
                 r["rx_id"], r["status"], r["doc_status"], r["first_seen"],
                 r["last_seen"]),
            )
            self.modules[(r["vehicle_id"], r["key"])] = r["id"]
        return len(rows)

    def load_signals(self) -> int:
        """Catalogue definitions first, then keys that exist only in the data."""
        assert self.src is not None and self.con is not None
        count = 0
        catalogue = build_catalogue(self.profile)
        for key, definition in catalogue.items():
            definition = definition.ensure_version()
            self.definitions[key] = definition
            row = definition.as_dict()
            layout = definition.layout
            self.con.execute(
                "INSERT INTO signal(key, name, unit, module_key, service, did, "
                "provenance, doc_status, confidence, formula, byte_offset, "
                "byte_length, byte_order, signed, scale, divisor, addend, "
                "plausible_min, plausible_max, plausible_basis, evidence_kind, "
                "evidence_source, evidence_artifact, limitations, falsified_by, "
                "definition_version, definition_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, definition.name, definition.unit, definition.ecu_key,
                 definition.service, definition.did, definition.provenance,
                 definition.doc_status, definition.confidence.value,
                 layout.describe() if layout else None,
                 layout.offset if layout else None,
                 layout.length if layout else None,
                 layout.byte_order.value if layout else None,
                 int(layout.signed) if layout else None,
                 layout.scale if layout else None,
                 layout.divisor if layout else None,
                 layout.addend if layout else None,
                 definition.plausible_min, definition.plausible_max,
                 definition.plausible_basis, definition.evidence_kind.value,
                 definition.evidence_source, definition.evidence_artifact,
                 definition.limitations, definition.falsified_by,
                 row.get("version"), json.dumps(row, sort_keys=True,
                                                ensure_ascii=False)),
            )
            self.signals[key] = self.con.execute(
                "SELECT signal_id FROM signal WHERE key=?", (key,)
            ).fetchone()["signal_id"]
            count += 1

        # Keys the registry no longer knows about (imports, retired decoders)
        # are still real observations and must not disappear in a rebuild.
        rows = self.src.execute(
            "SELECT m.key, m.ecu, m.service, m.pid, m.unit, m.provenance, "
            "m.doc_status "
            "FROM measurements m "
            "WHERE m.id = (SELECT MAX(m2.id) FROM measurements m2 "
            "              WHERE m2.key = m.key) "
            "ORDER BY m.key"
        ).fetchall()
        for r in rows:
            if r["key"] in self.signals:
                continue
            signal_id = self._insert_observed_signal(
                key=r["key"], module_key=r["ecu"], service=_as_int(r["service"]),
                did=_as_int(r["pid"]), unit=r["unit"],
                provenance=r["provenance"], doc_status=r["doc_status"])
            self.signals[r["key"]] = signal_id
            count += 1
        return count

    def _insert_observed_signal(self, key: str, module_key: str | None = None,
                                service: int | None = None, did: int | None = None,
                                unit: str | None = None,
                                provenance: str | None = None,
                                doc_status: str | None = None) -> int:
        assert self.con is not None
        payload = {
            "key": key,
            "name": key,
            "unit": unit,
            "confidence": "UNKNOWN",
            "source": "observed only; no catalogue definition",
        }
        self.con.execute(
            "INSERT INTO signal(key, name, unit, module_key, service, did, "
            "provenance, doc_status, confidence, definition_json) "
            "VALUES (?,?,?,?,?,?,?,?, 'UNKNOWN', ?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, key, unit, module_key, service, did, provenance, doc_status,
             json.dumps(payload, sort_keys=True, ensure_ascii=False)),
        )
        row = self.con.execute(
            "SELECT signal_id FROM signal WHERE key=?", (key,)
        ).fetchone()
        assert row is not None
        return int(row["signal_id"])

    def ensure_signal(self, key: str, **observed: Any) -> int:
        signal_id = self.signals.get(key)
        if signal_id is not None:
            return signal_id
        signal_id = self._insert_observed_signal(key, **observed)
        self.signals[key] = signal_id
        return signal_id

    def _source_id(self, provenance: str | None) -> int:
        assert self.con is not None
        label = provenance or "unknown"
        source_id = self.sources.get(label)
        if source_id is not None:
            return source_id
        kind = _provenance_kind(label)
        self.con.execute(
            "INSERT INTO data_source(provenance, kind) VALUES (?,?) "
            "ON CONFLICT(provenance) DO NOTHING", (label, kind))
        row = self.con.execute(
            "SELECT source_id FROM data_source WHERE provenance=?", (label,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"could not store provenance {label!r}")
        source_id = int(row["source_id"])
        self.sources[label] = source_id
        return source_id

    def _module_id(self, vehicle_id: int, key: str | None) -> int | None:
        if not key or key == "-":
            return None
        module_id = self.modules.get((vehicle_id, key))
        if module_id is not None:
            return module_id
        assert self.con is not None
        self.con.execute(
            "INSERT INTO module(vehicle_id, key, name) VALUES (?,?,?) "
            "ON CONFLICT(vehicle_id, key) DO NOTHING",
            (vehicle_id, key, key))
        module_id = self.con.execute(
            "SELECT module_id FROM module WHERE vehicle_id=? AND key=?",
            (vehicle_id, key)).fetchone()["module_id"]
        self.modules[(vehicle_id, key)] = int(module_id)
        return int(module_id)

    # -- facts --------------------------------------------------------------
    def load_observations(self) -> int:
        assert self.src is not None and self.con is not None
        if not _table_exists(self.src, "measurements"):
            return 0
        rows = self.src.execute(
            "SELECT id, vehicle_id, ts, ecu, service, pid, key, value, "
            "text_value, unit, provenance, decoding_method, doc_status, "
            "raw_response, success, error FROM measurements ORDER BY id"
        )
        count = 0
        for r in rows:
            raw = r["raw_response"]
            raw_hex = _hex_or_none(raw)
            raw_text = None if raw_hex is not None else _text_value(raw)
            signal_id = self.ensure_signal(
                r["key"], module_key=r["ecu"], service=_as_int(r["service"]),
                did=_as_int(r["pid"]), unit=r["unit"],
                provenance=r["provenance"], doc_status=r["doc_status"])
            self.con.execute(
                "INSERT INTO observation(vehicle_id, signal_id, module_id, "
                "observed_at, value_num, value_text, unit, source_id, "
                "decoding_method, doc_status, raw_hex, raw_text, success, error, "
                "source_measurement_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["vehicle_id"], signal_id, self._module_id(
                    r["vehicle_id"], r["ecu"]), r["ts"], r["value"],
                 r["text_value"], r["unit"], self._source_id(r["provenance"]),
                 r["decoding_method"], r["doc_status"], raw_hex, raw_text,
                 int(bool(r["success"])), r["error"], r["id"]),
            )
            count += 1
        return count

    def load_battery(self) -> tuple[int, int]:
        assert self.src is not None and self.con is not None
        snapshots = cells = 0
        if _table_exists(self.src, "battery_measurements"):
            rows = self.src.execute(
                "SELECT id, vehicle_id, ts, soc_abs_pct, soc_normal_pct, "
                "pack_voltage_v, pack_current_a, pack_power_kw, cell_min_v, "
                "cell_max_v, cell_delta_mv, battery_temp_c, energy_charged_kwh, "
                "energy_used_kwh, soh_pct, cac_ah, charge_mode "
                "FROM battery_measurements ORDER BY id"
            )
            for r in rows:
                self.con.execute(
                    "INSERT OR IGNORE INTO battery_snapshot(vehicle_id, "
                    "observed_at, soc_abs_pct, soc_normal_pct, pack_voltage_v, "
                    "pack_current_a, pack_power_kw, cell_min_v, cell_max_v, "
                    "cell_delta_mv, battery_temp_c, energy_charged_kwh, "
                    "energy_used_kwh, soh_pct, cac_ah, charge_mode, "
                    "source_battery_measurement_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["vehicle_id"], r["ts"], r["soc_abs_pct"],
                     r["soc_normal_pct"], r["pack_voltage_v"],
                     r["pack_current_a"], r["pack_power_kw"], r["cell_min_v"],
                     r["cell_max_v"], r["cell_delta_mv"], r["battery_temp_c"],
                     r["energy_charged_kwh"], r["energy_used_kwh"],
                     r["soh_pct"], r["cac_ah"], r["charge_mode"], r["id"]),
                )
                snapshots += 1
        if _table_exists(self.src, "cell_voltages"):
            rows = self.src.execute(
                "SELECT id, vehicle_id, ts, cell_index, voltage_v "
                "FROM cell_voltages ORDER BY id"
            )
            for r in rows:
                self.con.execute(
                    "INSERT OR IGNORE INTO cell_voltage(vehicle_id, observed_at, "
                    "cell_index, voltage_v, source_cell_voltage_id) "
                    "VALUES (?,?,?,?,?)",
                    (r["vehicle_id"], r["ts"], r["cell_index"], r["voltage_v"],
                     r["id"]),
                )
                cells += 1
        return snapshots, cells

    def load_charging(self) -> tuple[int, int]:
        assert self.src is not None and self.con is not None
        sessions = samples = 0
        id_map: dict[int, int] = {}
        if _table_exists(self.src, "charging_sessions"):
            rows = self.src.execute(
                "SELECT id, vehicle_id, charge_type, started_at, ended_at, "
                "start_soc, end_soc, duration_s, energy_estimate_kwh, "
                "max_power_kw, avg_battery_temp_c, status, notes "
                "FROM charging_sessions ORDER BY id"
            )
            for r in rows:
                cur = self.con.execute(
                    "INSERT INTO charging_session(vehicle_id, charge_type, "
                    "started_at, ended_at, start_soc, end_soc, duration_s, "
                    "energy_estimate_kwh, max_power_kw, avg_battery_temp_c, "
                    "status, notes, source_charging_session_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["vehicle_id"], r["charge_type"], r["started_at"],
                     r["ended_at"], r["start_soc"], r["end_soc"],
                     r["duration_s"], r["energy_estimate_kwh"],
                     r["max_power_kw"], r["avg_battery_temp_c"], r["status"],
                     r["notes"], r["id"]),
                )
                id_map[int(r["id"])] = int(cur.lastrowid)
                sessions += 1
        if _table_exists(self.src, "charging_samples"):
            rows = self.src.execute(
                "SELECT id, session_id, ts, voltage_v, current_a, power_kw, soc, "
                "battery_temp_c FROM charging_samples ORDER BY id"
            )
            for r in rows:
                session_id = id_map.get(int(r["session_id"]))
                if session_id is None:
                    continue
                self.con.execute(
                    "INSERT INTO charging_sample(charging_session_id, "
                    "observed_at, voltage_v, current_a, power_kw, soc, "
                    "battery_temp_c, source_charging_sample_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (session_id, r["ts"], r["voltage_v"], r["current_a"],
                     r["power_kw"], r["soc"], r["battery_temp_c"], r["id"]),
                )
                samples += 1
        return sessions, samples

    def load_faults(self) -> tuple[int, int, int, int, int]:
        assert self.src is not None and self.con is not None
        dtcs = occurrences = events = tx_rows = frames = 0
        dtc_map: dict[int, int] = {}
        if _table_exists(self.src, "dtcs"):
            rows = self.src.execute(
                "SELECT id, vehicle_id, ecu, code, description, categories, "
                "status, first_seen, last_seen, occurrence_count FROM dtcs "
                "ORDER BY id"
            )
            for r in rows:
                self.con.execute(
                    "INSERT OR IGNORE INTO dtc(vehicle_id, module_key, code, "
                    "description, categories_json, status, first_seen, last_seen, "
                    "occurrence_count, source_dtc_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r["vehicle_id"], r["ecu"], r["code"], r["description"],
                     r["categories"] or "[]", r["status"], r["first_seen"],
                     r["last_seen"], r["occurrence_count"], r["id"]),
                )
                row = self.con.execute(
                    "SELECT dtc_id FROM dtc WHERE source_dtc_id=?", (r["id"],)
                ).fetchone()
                if row is not None:
                    dtc_map[int(r["id"])] = int(row["dtc_id"])
                dtcs += 1
        if _table_exists(self.src, "dtc_occurrences"):
            rows = self.src.execute(
                "SELECT id, dtc_id, ts, freeze_frame FROM dtc_occurrences ORDER BY id"
            )
            for r in rows:
                dtc_id = dtc_map.get(int(r["dtc_id"]))
                if dtc_id is None:
                    continue
                self.con.execute(
                    "INSERT INTO dtc_occurrence(dtc_id, observed_at, "
                    "freeze_frame_json, source_dtc_occurrence_id) "
                    "VALUES (?,?,?,?)",
                    (dtc_id, r["ts"], r["freeze_frame"], r["id"]),
                )
                occurrences += 1
        if _table_exists(self.src, "diagnostic_events"):
            rows = self.src.execute(
                "SELECT id, vehicle_id, ts, kind, ecu, description, data "
                "FROM diagnostic_events ORDER BY id"
            )
            for r in rows:
                self.con.execute(
                    "INSERT INTO diagnostic_event(vehicle_id, observed_at, kind, "
                    "module_key, description, data_json, "
                    "source_diagnostic_event_id) VALUES (?,?,?,?,?,?,?)",
                    (r["vehicle_id"], r["ts"], r["kind"], r["ecu"],
                     r["description"], r["data"], r["id"]),
                )
                events += 1
        if _table_exists(self.src, "tx_log"):
            rows = self.src.execute(
                "SELECT id, ts, direction, ecu, payload, purpose FROM tx_log "
                "ORDER BY id"
            )
            for r in rows:
                self.con.execute(
                    "INSERT INTO tx_log(observed_at, direction, module_key, "
                    "payload, purpose, source_tx_log_id) VALUES (?,?,?,?,?,?)",
                    (r["ts"], r["direction"], r["ecu"], r["payload"],
                     r["purpose"], r["id"]),
                )
                tx_rows += 1
        if _table_exists(self.src, "captured_frames"):
            rows = self.src.execute(
                "SELECT id, vehicle_id, ts, ecu, request_bytes, "
                "response_raw_lines, response_payload, did_record, "
                "answer_source_id, session_state, succeeded, error, note "
                "FROM captured_frames ORDER BY id"
            )
            for r in rows:
                self.con.execute(
                    "INSERT INTO frame(vehicle_id, observed_at, module_key, "
                    "request_bytes, response_raw_lines, response_payload, "
                    "did_record, answer_source_id, session_state, succeeded, "
                    "error, note, source_frame_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["vehicle_id"], r["ts"], r["ecu"], r["request_bytes"],
                     r["response_raw_lines"], r["response_payload"],
                     r["did_record"], r["answer_source_id"],
                     r["session_state"], int(bool(r["succeeded"])), r["error"],
                     r["note"], r["id"]),
                )
                frames += 1
        return dtcs, occurrences, events, tx_rows, frames

    # -- capture archives ---------------------------------------------------
    def load_capture(self, path: Path) -> tuple[int, int]:
        assert self.con is not None
        doc = _parse_capture_json(path)
        metadata = _capture_metadata(doc)
        created = _capture_created(doc)
        computed_sha = sha256_file(path)
        rel = _relative_name(path)
        kind = _capture_kind(path)

        if _is_manifest(path):
            # A manifest describes an archive that is loaded separately. It is
            # not a capture in its own right, so attach its hashes/notes to the
            # archive and create nothing when the archive is absent.
            target = self.con.execute(
                "SELECT capture_id FROM capture WHERE file_name LIKE ? "
                "AND kind <> 'manifest' ORDER BY capture_id LIMIT 1",
                (f"%{_capture_stem(path)}.json",)
            ).fetchone()
            if target is None:
                return 0, 0
            self._merge_manifest(int(target["capture_id"]), doc)
            return 0, 0

        self.con.execute(
            "INSERT INTO capture(file_name, kind, schema_name, created_at, "
            "sha256, source, notes, record_count, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(file_name) DO UPDATE SET "
            "kind=excluded.kind, schema_name=excluded.schema_name, "
            "created_at=excluded.created_at, sha256=excluded.sha256, "
            "source=excluded.source, notes=excluded.notes, "
            "metadata_json=excluded.metadata_json",
            (rel, kind, _schema_name(doc), created, computed_sha,
             _capture_source(doc), _capture_notes(doc), 0,
             json.dumps(metadata, sort_keys=True, ensure_ascii=False)),
        )
        capture_id = self.con.execute(
            "SELECT capture_id FROM capture WHERE file_name=?", (rel,)
        ).fetchone()["capture_id"]

        if _is_validation(path):
            checks = self._load_validation(capture_id, path, doc)
            self.con.execute(
                "UPDATE capture SET record_count=? WHERE capture_id=?",
                (len(doc.get("verdicts", [])), capture_id))
            return 1, checks

        records = _normalise_records(path, doc)
        inserted = 0
        for record in records:
            inserted += self._insert_capture_records(capture_id, record)
        self.con.execute(
            "UPDATE capture SET record_count=? WHERE capture_id=?",
            (inserted, capture_id))
        return 1, inserted

    def _merge_manifest(self, capture_id: int, doc: dict) -> None:
        assert self.con is not None
        row = self.con.execute(
            "SELECT metadata_json, notes FROM capture WHERE capture_id=?",
            (capture_id,)).fetchone()
        metadata = (json.loads(row["metadata_json"])
                    if row and row["metadata_json"] else {})
        metadata["manifest"] = doc
        notes = row["notes"] if row else None
        for key in ("notes", "safety"):
            value = doc.get(key)
            if value:
                notes = f"{notes}; {value}" if notes else str(value)
        archive_sha = doc.get("archive_sha256") or doc.get("sha256")
        if archive_sha:
            metadata["manifest_archive_sha256"] = archive_sha
        self.con.execute(
            "UPDATE capture SET metadata_json=?, notes=? WHERE capture_id=?",
            (json.dumps(metadata, sort_keys=True, ensure_ascii=False), notes,
             capture_id),
        )

    def _insert_capture_records(self, capture_id: int, record: dict) -> int:
        assert self.con is not None
        signal_key = record.get("signal_key")
        raw_hex = _hex_or_none(record.get("raw"))
        base = {
            "capture_id": capture_id,
            "observed_at": record.get("observed_at"),
            "elapsed_s": record.get("elapsed_s"),
            "raw_hex": raw_hex,
            "unit": record.get("unit"),
            "expected_num": record.get("expected_num"),
            "expected_text": record.get("expected_text"),
            "source": record.get("source"),
            "metadata_json": json.dumps(record.get("metadata", {}),
                                        sort_keys=True, ensure_ascii=False),
        }
        if signal_key:
            return self._insert_capture_record(base, signal_key,
                                               record.get("decoded_num"),
                                               record.get("decoded_text"))

        # Passive btsnoop records carry a DID and a data-record payload but no
        # key. Decode every registry spec for that DID so the archive is as
        # queryable as the live rows, and fall back to `did:0xNNNN` when no
        # decoder applies (raw-only evidence is still evidence).
        did = _as_int(record.get("did"))
        payload = record.get("raw")
        decoded_any = False
        inserted = 0
        registry = getattr(self, "_registry_by_did", None)
        if registry is None:
            registry = _registry_by_did(self.profile)
            self._registry_by_did = registry
        if did is not None and payload:
            raw_bytes = bytes.fromhex(raw_hex) if raw_hex else b""
            seen: set[str] = set()
            for spec in registry.get(did, ()):
                if spec.key in seen:
                    continue
                seen.add(spec.key)
                value = spec.decode_value(raw_bytes)
                if value is None:
                    continue
                decoded_any = True
                inserted += self._insert_capture_record(
                    base, spec.key, _as_float(value), _text_value(value))
        if not decoded_any:
            key = f"did:0x{did:04X}" if did is not None else "unknown"
            inserted += self._insert_capture_record(
                base, key, None, None)
        return inserted

    def _insert_capture_record(self, base: dict, signal_key: str,
                               decoded_num: float | None,
                               decoded_text: str | None) -> int:
        assert self.con is not None
        signal_id = self.ensure_signal(signal_key)
        self.con.execute(
            "INSERT INTO capture_record(capture_id, observed_at, elapsed_s, "
            "signal_id, signal_key, raw_hex, decoded_num, decoded_text, unit, "
            "expected_num, expected_text, source, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (base["capture_id"], base["observed_at"], base["elapsed_s"],
             signal_id, signal_key, base["raw_hex"], decoded_num,
             decoded_text, base["unit"], base["expected_num"],
             base["expected_text"], base["source"], base["metadata_json"]),
        )
        return 1

    def _load_validation(self, capture_id: int, path: Path,
                         doc: dict) -> int:
        assert self.con is not None
        target_stem = _capture_stem(path)
        target = self.con.execute(
            "SELECT capture_id, file_name FROM capture "
            "WHERE file_name LIKE ? AND kind <> 'validation_report' "
            "ORDER BY capture_id LIMIT 1", (f"%{target_stem}%",)
        ).fetchone()
        if target is None:
            return 0
        method = "capture_validation_strict" if "strict" in path.name else \
            "capture_validation"
        checks = 0
        used_records: set[int] = set()
        for verdict in doc.get("verdicts", []):
            key = verdict.get("key")
            raw_hex = _hex_or_none(verdict.get("raw"))
            if not key:
                continue
            matches = self.con.execute(
                "SELECT capture_record_id, decoded_num, unit FROM capture_record "
                "WHERE capture_id=? AND signal_key=? AND COALESCE(raw_hex,'')=? "
                "ORDER BY capture_record_id", (target["capture_id"], key,
                                               raw_hex or "")
            ).fetchall()
            match = next(
                (m for m in matches
                 if int(m["capture_record_id"]) not in used_records), None)
            if match is None:
                continue
            used_records.add(int(match["capture_record_id"]))
            expected = _as_float(verdict.get("expected"))
            decoded = _as_float(verdict.get("value"))
            error = (abs(decoded - expected)
                     if decoded is not None and expected is not None
                     else None)
            self.con.execute(
                "INSERT INTO expectation_check(capture_record_id, method, "
                "expected_num, decoded_num, absolute_error, unit, verdict, "
                "issue, source_file) VALUES (?,?,?,?,?,?,?,?,?)",
                (match["capture_record_id"], method, expected, decoded,
                 error, verdict.get("unit") or match["unit"],
                 str(verdict.get("result", "INDETERMINATE")),
                 verdict.get("issue"), _relative_name(path)),
            )
            checks += 1
        return checks

    # -- orchestration ------------------------------------------------------
    def build(self, captures: list[Path], vacuum: bool) -> dict:
        self.connect()
        try:
            self.summary["vehicles"] = self.load_vehicles()
            self.load_modules()
            self.summary["signals"] = self.load_signals()
            self.summary["observations"] = self.load_observations()
            (self.summary["battery_snapshots"],
             self.summary["cell_voltages"]) = self.load_battery()
            (self.summary["charging_sessions"],
             self.summary["charging_samples"]) = self.load_charging()
            (self.summary["dtcs"], self.summary["dtc_occurrences"],
             self.summary["diagnostic_events"], self.summary["tx_log"],
             self.summary["frames"]) = self.load_faults()

            capture_count = record_count = check_count = 0
            # Archives first: validation reports and manifests refer to them.
            ordered = sorted(captures, key=lambda p: (
                _is_manifest(p), _is_validation(p), str(p)))
            for path in ordered:
                c, r = self.load_capture(path)
                capture_count += c
                if _is_validation(path):
                    check_count += r
                else:
                    record_count += r
            self.summary["captures"] = capture_count
            self.summary["capture_records"] = record_count
            self.summary["expectation_checks"] = check_count
            # Loading can introduce dimensions the source never knew about:
            # imported third-party modules, and capture-only signal keys. Report
            # what actually landed in the warehouse, not what a loader returned.
            self.summary["modules"] = self.con.execute(
                "SELECT COUNT(*) FROM module").fetchone()[0]
            self.summary["signals"] = self.con.execute(
                "SELECT COUNT(*) FROM signal").fetchone()[0]

            compiled = build_default_registry(self.profile)
            meta = {
                "schema_version": str(SCHEMA_VERSION),
                "generated_at": utcnow(),
                "source_db": str(self.source),
                "source_db_sha256": sha256_file(self.source),
                "profile": self.profile,
                "catalogue_version": CATALOGUE_VERSION,
                "registry_signals": str(len(compiled.all())),
                "capture_files": str(len(captures)),
            }
            for key, value in meta.items():
                self.con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
                    (key, value))
            self.con.commit()
            self._integrity_check()
            self.summary["integrity"] = "ok"
            if vacuum:
                self.con.execute("VACUUM")
            self.con.close()
            self.con = None
            self.src.close()
            self.src = None
            # A stale WAL/SHM pair beside the target would be interpreted as
            # belonging to the new file. Remove them before the atomic replace.
            for suffix in ("-wal", "-shm"):
                Path(str(self.out) + suffix).unlink(missing_ok=True)
            os.replace(self.tmp, self.out)
            self.summary["out"] = str(self.out)
            self.summary["size_bytes"] = self.out.stat().st_size
            return self.summary
        except Exception:
            self.close()
            Path(str(self.tmp) + "-wal").unlink(missing_ok=True)
            Path(str(self.tmp) + "-shm").unlink(missing_ok=True)
            self.tmp.unlink(missing_ok=True)
            raise

    def _integrity_check(self) -> None:
        assert self.con is not None
        problems = self.con.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(
                f"foreign key violations after load: {len(problems)} "
                f"(first: {tuple(problems[0])})")
        result = self.con.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check failed: {result}")


# ---------------------------------------------------------------------------
# Capture normalisation
# ---------------------------------------------------------------------------

def _relative_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _registry_by_did(profile: str) -> dict[int, list]:
    registry = build_default_registry(profile)
    by_did: dict[int, list] = {}
    for spec in registry.all():
        if spec.did is None:
            continue
        by_did.setdefault(spec.did, []).append(spec)
    return by_did


def _schema_name(doc: Any) -> str | None:
    return doc.get("schema") if isinstance(doc, dict) else None


def _capture_created(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    for key in ("created_utc", "capture_date", "started_utc", "generated_utc"):
        value = doc.get(key)
        if value:
            return _iso_in_text(str(value)) or str(value)
    return None


def _capture_source(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    value = doc.get("source")
    if isinstance(value, dict):
        return _text_value(value)
    if value:
        return str(value)
    return None


def _capture_notes(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    for key in ("purpose", "notes", "safety", "privacy"):
        value = doc.get(key)
        if value:
            return _text_value(value)
    return None


def _capture_metadata(doc: Any) -> dict:
    """Everything except the bulky repeating records/verdicts payloads."""
    if not isinstance(doc, dict):
        return {"type": type(doc).__name__}
    skip = {"records", "results", "captures", "verdicts"}
    return {k: v for k, v in doc.items() if k not in skip}


def _normalise_records(path: Path, doc: Any) -> list[dict]:
    """Convert the archive's own shape into the loader's record vocabulary."""
    if isinstance(doc, list):
        return [_basic_record(item) for item in doc if isinstance(item, dict)]
    if not isinstance(doc, dict):
        return []
    kind = _capture_kind(path)
    if kind == "gateway_scan":
        return _gateway_records(doc)
    if kind == "passive_capture":
        return _passive_records(doc)
    if kind == "expected_values":
        return _expected_records(doc)
    if kind == "timed_watch":
        return _watch_records(doc)
    for key in ("records", "results", "captures"):
        if isinstance(doc.get(key), list):
            return [_basic_record(item) for item in doc[key]
                    if isinstance(item, dict)]
    return []


def _basic_record(item: dict) -> dict:
    return {
        "signal_key": item.get("key"),
        "raw": item.get("raw"),
        "observed_at": item.get("ts"),
        "elapsed_s": item.get("elapsed_s") or item.get("offset_s"),
        "decoded_num": _as_float(item.get("value")),
        "decoded_text": _text_value(item.get("value"))
        if _as_float(item.get("value")) is None else None,
        "unit": item.get("unit"),
        "expected_num": _as_float(item.get("expected")),
        "expected_text": _text_value(item.get("expected")),
        "source": item.get("source"),
        "metadata": item,
    }


def _gateway_records(doc: dict) -> list[dict]:
    observed_at = _iso_in_text(str(doc.get("started_utc") or ""))
    records: list[dict] = []
    for result in doc.get("results", []):
        if not isinstance(result, dict):
            continue
        raw = result.get("record_hex")
        decoded = result.get("decoded") or []
        metadata = {
            "did": result.get("did"),
            "label": result.get("label"),
            "request": result.get("request"),
            "tx": result.get("tx"),
            "rx": result.get("rx"),
            "payload_len": result.get("payload_len"),
            "error": result.get("error"),
        }
        if decoded:
            for item in decoded:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                records.append({
                    "signal_key": item.get("key"),
                    "raw": raw,
                    "observed_at": observed_at,
                    "elapsed_s": None,
                    "decoded_num": _as_float(value),
                    "decoded_text": _text_value(value)
                    if _as_float(value) is None else None,
                    "unit": item.get("unit"),
                    "expected_num": None,
                    "expected_text": None,
                    "source": "gateway scan",
                    "metadata": {**metadata, "decoded": item},
                })
        else:
            records.append({
                "signal_key": None,
                "did": result.get("did"),
                "raw": raw,
                "observed_at": observed_at,
                "elapsed_s": None,
                "decoded_num": None,
                "decoded_text": None,
                "unit": None,
                "expected_num": None,
                "expected_text": None,
                "source": "gateway scan",
                "metadata": metadata,
            })
    return records


def _passive_records(doc: dict) -> list[dict]:
    capture_date = doc.get("capture_date")
    records: list[dict] = []
    for item in doc.get("records", []):
        if not isinstance(item, dict):
            continue
        records.append({
            "signal_key": None,
            "did": item.get("did"),
            "raw": item.get("payload"),
            "observed_at": capture_date,
            "elapsed_s": item.get("offset_s"),
            "decoded_num": None,
            "decoded_text": None,
            "unit": None,
            "expected_num": None,
            "expected_text": None,
            "source": f"passive btsnoop offset {item.get('offset_s')}",
            "metadata": item,
        })
    return records


def _expected_records(doc: dict) -> list[dict]:
    records: list[dict] = []
    for item in doc.get("captures", []):
        if not isinstance(item, dict):
            continue
        records.append({
            "signal_key": item.get("key"),
            "raw": item.get("raw"),
            "observed_at": None,
            "elapsed_s": None,
            "decoded_num": None,
            "decoded_text": None,
            "unit": item.get("unit"),
            "expected_num": _as_float(item.get("expected")),
            "expected_text": _text_value(item.get("expected")),
            "source": item.get("source"),
            "metadata": item,
        })
    return records


def _watch_records(doc: dict) -> list[dict]:
    records: list[dict] = []
    for item in doc.get("records", []):
        if not isinstance(item, dict):
            continue
        for field, key in WATCH_FIELD_KEYS.items():
            value = item.get(field)
            if not isinstance(value, dict):
                continue
            decoded = value.get("value")
            records.append({
                "signal_key": key,
                "raw": value.get("raw"),
                "observed_at": item.get("ts"),
                "elapsed_s": item.get("elapsed_s"),
                "decoded_num": _as_float(decoded),
                "decoded_text": _text_value(decoded)
                if _as_float(decoded) is None else None,
                "unit": None,
                "expected_num": None,
                "expected_text": None,
                "source": "watch capture",
                "metadata": {"field": field, **item},
            })
    return records


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build(source: str | Path = ROOT / "data" / "odb_scanner.db",
          out: str | Path = DEFAULT_DB,
          profile: str = "meb",
          captures: Iterable[str] | None = None,
          include_captures: bool = True,
          force: bool = False,
          vacuum: bool = True) -> dict:
    source_path = Path(source).resolve()
    out_path = Path(out).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source database not found: {source_path}")
    if source_path == out_path:
        raise ValueError("source and output must be different files")
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} already exists; pass force=True / --force to replace it")
    if not SCHEMA_PATH.is_file():
        raise FileNotFoundError(f"schema not found: {SCHEMA_PATH}")

    before = sha256_file(source_path)
    capture_paths = resolve_captures(captures) if include_captures else []
    builder = _Builder(source_path, out_path, profile)
    summary = builder.build(capture_paths, vacuum)
    after = sha256_file(source_path)
    if before != after:
        raise RuntimeError(
            "source database changed during the build; refusing to trust output")
    summary["source_unchanged"] = True
    return summary


def _print_summary(summary: dict) -> None:
    print(f"Built {summary['out']} "
          f"({summary['size_bytes'] / 1024 / 1024:.1f} MiB)")
    print(f"  source:      {summary.get('source_db', '')}")
    print(f"  vehicles:    {summary['vehicles']}")
    print(f"  modules:     {summary['modules']}")
    print(f"  signals:     {summary['signals']}")
    print(f"  observations:{summary['observations']:>8}")
    print(f"  cells:       {summary['cell_voltages']}")
    print(f"  snapshots:   {summary['battery_snapshots']}")
    print(f"  captures:    {summary['captures']} "
          f"({summary['capture_records']} records, "
          f"{summary['expectation_checks']} checks)")
    print(f"  integrity:   {summary['integrity']} "
          f"(source unchanged: {summary.get('source_unchanged')})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the normalised observation database from collected data.")
    parser.add_argument("--source", default=None,
                        help="live collector database (default: config database.path)")
    parser.add_argument("--out", default=str(DEFAULT_DB),
                        help=f"output database (default: {DEFAULT_DB})")
    parser.add_argument("--profile", default=None,
                        help="decoder profile (default: config vehicle.did_profile)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--captures", nargs="*", default=None,
                        help="capture JSON files/directories; default: known archives")
    parser.add_argument("--no-captures", action="store_true",
                        help="skip JSON evidence archives")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing output database")
    parser.add_argument("--no-vacuum", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="print the summary as JSON")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    source = args.source or cfg.db_path
    profile = args.profile or cfg.get("vehicle.did_profile", "meb")
    try:
        summary = build(
            source=source, out=args.out, profile=profile,
            captures=args.captures, include_captures=not args.no_captures,
            force=args.force, vacuum=not args.no_vacuum)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
