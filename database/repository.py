"""Repository: all database access. Raw responses always preserved."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import SCHEMA_SQL, SCHEMA_VERSION


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utciso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _iso_days_ago(days: float) -> str:
    return utciso(datetime.now(timezone.utc) - timedelta(days=days))


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class Repository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- vehicle / ecus ------------------------------------------------------
    def ensure_vehicle(self, vin: str, make="", model="", variant="", year=None) -> int:
        row = self.conn.execute("SELECT id FROM vehicles WHERE vin=?", (vin,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO vehicles(vin, make, model, variant, year, first_seen) "
            "VALUES (?,?,?,?,?,?)",
            (vin, make, model, variant, year, utcnow()),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_ecu(self, vehicle_id: int, key: str, name: str, tx: int, rx: int,
                   status: str, doc_status: str) -> None:
        now = utcnow()
        self.conn.execute(
            "INSERT INTO ecus(vehicle_id, key, name, tx_id, rx_id, status, "
            "doc_status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(vehicle_id, key) DO UPDATE SET status=excluded.status, "
            "last_seen=excluded.last_seen",
            (vehicle_id, key, name, tx, rx, status, doc_status, now, now),
        )
        self.conn.commit()

    def list_ecus(self, vehicle_id: int):
        return self.conn.execute(
            "SELECT * FROM ecus WHERE vehicle_id=? ORDER BY key", (vehicle_id,)
        ).fetchall()

    # -- tx log (safety manifest) --------------------------------------------
    def log_tx(self, ts: str | None = None, **kw) -> None:
        self.conn.execute(
            "INSERT INTO tx_log(ts, direction, ecu, payload, purpose) "
            "VALUES (?,?,?,?,?)",
            (ts or utcnow(), kw.get("direction"), kw.get("ecu"),
             kw.get("payload"), kw.get("purpose")),
        )
        self.conn.commit()

    # -- measurements ---------------------------------------------------------
    def record_measurement(self, vehicle_id: int, ts: str, key: str, ecu: str,
                           service: str, pid: str, unit: str, provenance: str,
                           decoding_method: str, doc_status: str,
                           raw_response: str, value=None, text_value=None,
                           success: bool = True, error: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO measurements(vehicle_id, ts, ecu, service, pid, key, "
            "value, text_value, unit, provenance, decoding_method, doc_status, "
            "raw_response, success, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vehicle_id, ts, ecu, service, pid, key, value, text_value, unit,
             provenance, decoding_method, doc_status, raw_response,
             int(success), error),
        )
        self.conn.commit()
        return cur.lastrowid

    def measurement_series(self, key: str, since: str, vehicle_id: int | None = None):
        sql = ("SELECT ts, value, unit, provenance FROM measurements "
               "WHERE key=? AND ts>=? AND success=1 AND value IS NOT NULL")
        args: list = [key, since]
        if vehicle_id:
            sql += " AND vehicle_id=?"
            args.append(vehicle_id)
        sql += " ORDER BY ts"
        return self.conn.execute(sql, args).fetchall()

    def latest_measurements(self, vehicle_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT key, value, text_value, unit, provenance, ts, doc_status "
            "FROM measurements m WHERE vehicle_id=? AND success=1 AND id IN "
            "(SELECT MAX(id) FROM measurements WHERE vehicle_id=? AND success=1 "
            "GROUP BY key)",
            (vehicle_id, vehicle_id),
        ).fetchall()
        return {r["key"]: dict(r) for r in rows}

    # -- battery snapshots ------------------------------------------------------
    def record_battery_snapshot(self, vehicle_id: int, ts: str, **cols) -> None:
        fields = ["soc_abs_pct", "soc_normal_pct", "pack_voltage_v",
                  "pack_current_a", "pack_power_kw", "cell_min_v", "cell_max_v",
                  "cell_delta_mv", "battery_temp_c", "energy_charged_kwh",
                  "energy_used_kwh", "soh_pct", "cac_ah", "charge_mode"]
        vals = [cols.get(f) for f in fields]
        self.conn.execute(
            f"INSERT INTO battery_measurements(vehicle_id, ts, "
            f"{', '.join(fields)}) VALUES (?, ?, {', '.join('?' * len(fields))})",
            [vehicle_id, ts] + vals,
        )
        self.conn.commit()

    def battery_history(self, days: int = 30, vehicle_id: int | None = None):
        since = _iso_days_ago(days)
        sql = "SELECT * FROM battery_measurements WHERE ts>=?"
        args: list = [since]
        if vehicle_id:
            sql += " AND vehicle_id=?"
            args.append(vehicle_id)
        sql += " ORDER BY ts"
        return self.conn.execute(sql, args).fetchall()

    def record_cell_voltages(self, vehicle_id: int, ts: str,
                             voltages: list[float]) -> None:
        self.conn.executemany(
            "INSERT INTO cell_voltages(vehicle_id, ts, cell_index, voltage_v) "
            "VALUES (?,?,?,?)",
            [(vehicle_id, ts, i, v) for i, v in enumerate(voltages)],
        )
        self.conn.commit()

    # -- charging sessions -------------------------------------------------------
    def open_session(self, vehicle_id: int, ts: str, charge_type: str,
                     start_soc=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO charging_sessions(vehicle_id, charge_type, started_at, "
            "start_soc, status) VALUES (?,?,?,?, 'open')",
            (vehicle_id, charge_type, ts, start_soc),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_charging_sample(self, session_id: int, ts: str, voltage_v=None,
                            current_a=None, power_kw=None, soc=None,
                            battery_temp_c=None) -> None:
        self.conn.execute(
            "INSERT INTO charging_samples(session_id, ts, voltage_v, current_a, "
            "power_kw, soc, battery_temp_c) VALUES (?,?,?,?,?,?,?)",
            (session_id, ts, voltage_v, current_a, power_kw, soc, battery_temp_c),
        )
        self.conn.commit()

    def close_session(self, session_id: int, ts: str, end_soc=None) -> None:
        sess = self.conn.execute(
            "SELECT * FROM charging_sessions WHERE id=?", (session_id,)
        ).fetchone()
        samples = self.conn.execute(
            "SELECT power_kw, battery_temp_c FROM charging_samples "
            "WHERE session_id=? ORDER BY ts", (session_id,)
        ).fetchall()
        powers = [s["power_kw"] for s in samples if s["power_kw"] is not None]
        temps = [s["battery_temp_c"] for s in samples
                 if s["battery_temp_c"] is not None]
        duration = (_parse_ts(ts) - _parse_ts(sess["started_at"])).total_seconds()
        energy = (sum(p for p in powers if p > 0) * duration / 3600.0
                  if powers else None)
        self.conn.execute(
            "UPDATE charging_sessions SET ended_at=?, end_soc=?, duration_s=?, "
            "energy_estimate_kwh=?, max_power_kw=?, avg_battery_temp_c=?, "
            "status='completed' WHERE id=?",
            (ts, end_soc, duration, energy,
             max(powers) if powers else None,
             sum(temps) / len(temps) if temps else None, session_id),
        )
        self.conn.commit()

    def open_session_id(self, vehicle_id: int):
        row = self.conn.execute(
            "SELECT id FROM charging_sessions WHERE vehicle_id=? AND status='open' "
            "ORDER BY started_at DESC LIMIT 1", (vehicle_id,)
        ).fetchone()
        return row["id"] if row else None

    def charging_sessions(self, days: int = 90, vehicle_id: int | None = None):
        sql = ("SELECT * FROM charging_sessions WHERE status='completed' "
               "AND started_at>=?")
        args: list = [_iso_days_ago(days)]
        if vehicle_id:
            sql += " AND vehicle_id=?"
            args.append(vehicle_id)
        sql += " ORDER BY started_at"
        return self.conn.execute(sql, args).fetchall()

    # -- DTCs ---------------------------------------------------------------
    def upsert_dtc(self, vehicle_id: int, ts: str, ecu: str, code: str,
                   description: str, categories: list[str],
                   status: str = "unknown",
                   freeze_frame: dict | None = None) -> int:
        row = self.conn.execute(
            "SELECT id FROM dtcs WHERE vehicle_id=? AND ecu=? AND code=?",
            (vehicle_id, ecu, code),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE dtcs SET last_seen=?, occurrence_count=occurrence_count+1, "
                "status=? WHERE id=?", (ts, status, row["id"]),
            )
            dtc_id = row["id"]
        else:
            cur = self.conn.execute(
                "INSERT INTO dtcs(vehicle_id, ecu, code, description, categories, "
                "status, first_seen, last_seen, occurrence_count) "
                "VALUES (?,?,?,?,?,?,?,?,'1')",
                (vehicle_id, ecu, code, description,
                 json.dumps(categories), status, ts, ts),
            )
            dtc_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO dtc_occurrences(dtc_id, ts, freeze_frame) VALUES (?,?,?)",
            (dtc_id, ts, json.dumps(freeze_frame) if freeze_frame else None),
        )
        self.conn.commit()
        return dtc_id

    def dtc_list(self, vehicle_id: int):
        return self.conn.execute(
            "SELECT * FROM dtcs WHERE vehicle_id=? ORDER BY last_seen DESC",
            (vehicle_id,),
        ).fetchall()

    def dtc_occurrences(self, vehicle_id: int, since: str | None = None):
        sql = ("SELECT o.*, d.code, d.ecu, d.categories FROM dtc_occurrences o "
               "JOIN dtcs d ON d.id=o.dtc_id WHERE d.vehicle_id=?")
        args: list = [vehicle_id]
        if since:
            sql += " AND o.ts>=?"
            args.append(since)
        sql += " ORDER BY o.ts"
        return self.conn.execute(sql, args).fetchall()

    # -- events / analysis / reports / anomalies -------------------------------
    def add_event(self, vehicle_id: int, ts: str, kind: str, description: str,
                  ecu=None, data: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO diagnostic_events(vehicle_id, ts, kind, ecu, "
            "description, data) VALUES (?,?,?,?,?,?)",
            (vehicle_id, ts, kind, ecu, description,
             json.dumps(data) if data else None),
        )
        self.conn.commit()

    def add_analysis(self, vehicle_id: int, analysis_type: str, subject: str,
                     window_days: int, result: dict) -> None:
        self.conn.execute(
            "INSERT INTO analysis_results(vehicle_id, ts, analysis_type, "
            "subject, window_days, result_json) VALUES (?,?,?,?,?,?)",
            (vehicle_id, utcnow(), analysis_type, subject, window_days,
             json.dumps(result, default=str)),
        )
        self.conn.commit()

    def add_llm_report(self, vehicle_id: int, question: str, model: str,
                       report_text: str, context: dict, warnings: list) -> None:
        self.conn.execute(
            "INSERT INTO llm_reports(vehicle_id, ts, question, model, "
            "report_text, context_json, warnings) VALUES (?,?,?,?,?,?,?)",
            (vehicle_id, utcnow(), question, model, report_text,
             json.dumps(context, default=str), json.dumps(warnings)),
        )
        self.conn.commit()

    def llm_reports(self, vehicle_id: int, limit: int = 20):
        return self.conn.execute(
            "SELECT * FROM llm_reports WHERE vehicle_id=? ORDER BY ts DESC "
            "LIMIT ?", (vehicle_id, limit),
        ).fetchall()

    def add_anomaly(self, vehicle_id: int, ts: str, metric: str, value: float,
                    mean: float, std: float, z: float, direction: str,
                    description: str) -> None:
        self.conn.execute(
            "INSERT INTO anomalies(vehicle_id, ts, metric, value, baseline_mean, "
            "baseline_std, zscore, direction, description) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (vehicle_id, ts, metric, value, mean, std, z, direction, description),
        )
        self.conn.commit()

    def anomalies(self, vehicle_id: int, days: int = 30):
        return self.conn.execute(
            "SELECT * FROM anomalies WHERE vehicle_id=? AND ts>=? ORDER BY ts",
            (vehicle_id, _iso_days_ago(days)),
        ).fetchall()



