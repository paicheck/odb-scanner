"""Data-quality snapshot for the dashboard: how much of what is shown to trust.

Every chart already carries per-series evidence (decoder verification,
plausibility, provenance, gaps). What was missing is the page-level answer to
"how good is this data, overall, right now?" -- when the car was last read, in
how many sessions, how many reads it answered, how far the decoders behind the
signals are verified, and how much of the window is imported, simulated or
implausible. This module computes it; the overview renders it.

Only the vehicle's own readings count as "live": imported (Car Scanner) and
simulated rows are reported separately, never mixed into the live figures.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database.repository import IMPORTED_PREFIX

LEVELS = ("verified", "provisional", "unknown", "invalid")


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def sessions(timestamps: list[str], gap_s: float) -> list[dict]:
    """Group sorted timestamps into recording sessions split at gaps > gap_s.

    The same threshold breaks chart lines and collapses idle time on the
    dashboard's session axis, so "a session" means the same thing everywhere.
    """
    out: list[dict] = []
    for ts in timestamps:
        t = _parse(ts)
        if t is None:
            continue
        if out and (t - out[-1]["_end"]).total_seconds() <= gap_s:
            out[-1]["_end"] = t
            out[-1]["end"] = ts
            out[-1]["readings"] += 1
        else:
            out.append({"start": ts, "end": ts, "_start": t, "_end": t,
                        "readings": 1})
    for s in out:
        s["duration_s"] = round((s.pop("_end") - s.pop("_start")).total_seconds())
    return out


def snapshot(repo, vehicle_id: int, days: int, profile: str,
             gap_s: float) -> dict:
    """The overview's data-quality figures for the last `days` days."""
    from decoders.quality import quality_registry

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    live = ("provenance NOT LIKE ? AND provenance <> 'simulated'")
    q = repo.conn.execute

    live_ts = [r[0] for r in q(
        f"SELECT ts FROM measurements WHERE vehicle_id=? AND ts>=? AND success=1 "
        f"AND {live} ORDER BY ts", (vehicle_id, since, IMPORTED_PREFIX + "%"))]
    sess = sessions(live_ts, gap_s)

    reads = q(
        f"SELECT COUNT(*) AS n, SUM(success) AS ok FROM measurements "
        f"WHERE vehicle_id=? AND ts>=? AND {live}",
        (vehicle_id, since, IMPORTED_PREFIX + "%")).fetchone()
    n_reads, n_ok = reads["n"] or 0, reads["ok"] or 0

    by_prov = {r["kind"]: r["n"] for r in q(
        "SELECT CASE WHEN provenance LIKE ? THEN 'imported' "
        "WHEN provenance = 'simulated' THEN 'simulated' ELSE 'live' END AS kind, "
        "COUNT(*) AS n FROM measurements WHERE vehicle_id=? AND ts>=? "
        "AND success=1 GROUP BY kind", (IMPORTED_PREFIX + "%", vehicle_id, since))}

    registry = quality_registry(profile)
    keys = [r[0] for r in q(
        f"SELECT DISTINCT key FROM measurements WHERE vehicle_id=? AND ts>=? "
        f"AND success=1 AND {live}", (vehicle_id, since, IMPORTED_PREFIX + "%"))]
    evidence = {level: 0 for level in LEVELS}
    uncatalogued = 0
    implausible: list[dict] = []
    for key in keys:
        quality = registry.get(key)
        if quality is None:
            uncatalogued += 1
            continue
        evidence[quality.verification.value] = \
            evidence.get(quality.verification.value, 0) + 1
        lo, hi = quality.plausible_min, quality.plausible_max
        if lo is None or hi is None:
            continue
        n = q(
            f"SELECT COUNT(*) FROM measurements WHERE vehicle_id=? AND key=? "
            f"AND ts>=? AND success=1 AND {live} AND typeof(value) IN "
            f"('integer','real') AND (value < ? OR value > ?)",
            (vehicle_id, key, since, IMPORTED_PREFIX + "%", lo, hi)).fetchone()[0]
        if n:
            implausible.append({"key": key, "count": n, "range": [lo, hi]})
    implausible.sort(key=lambda r: -r["count"])

    return {
        "days": days,
        "last_ts": live_ts[-1] if live_ts else None,
        "sessions": len(sess),
        "latest_session": sess[-1] if sess else None,
        "reads": n_reads,
        "reads_ok": n_ok,
        "success_pct": round(100.0 * n_ok / n_reads, 1) if n_reads else None,
        "live_rows": by_prov.get("live", 0),
        "imported_rows": by_prov.get("imported", 0),
        "simulated_rows": by_prov.get("simulated", 0),
        "signals": len(keys),
        "evidence": evidence,
        "uncatalogued": uncatalogued,
        "implausible": implausible,
        "implausible_rows": sum(r["count"] for r in implausible),
        "retired_dids": len(repo.unsupported_dids(vehicle_id)),
    }
