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


def sessions_from_minutes(minutes: list[dict], gap_s: float) -> list[dict]:
    """`sessions` over per-minute activity (Repository.activity_minutes).

    Exact for any gap of a minute or more: readings inside one minute are
    never further apart than that, and between minutes the gap compared is
    the one between the last reading of one and the first of the next.
    """
    out: list[dict] = []
    for m in minutes:
        first, last = _parse(m["first_ts"]), _parse(m["last_ts"])
        if first is None or last is None:
            continue
        if out and (first - out[-1]["_end"]).total_seconds() <= gap_s:
            out[-1]["_end"] = last
            out[-1]["end"] = m["last_ts"]
            out[-1]["readings"] += m["n"]
        else:
            out.append({"start": m["first_ts"], "end": m["last_ts"],
                        "_start": first, "_end": last, "readings": m["n"]})
    for s in out:
        s["duration_s"] = round((s.pop("_end") - s.pop("_start")).total_seconds())
    return out


def snapshot(repo, vehicle_id: int, days: int, profile: str,
             gap_s: float) -> dict:
    """The overview's data-quality figures for the last `days` days.

    Counts come from the repository's measurement summaries, so this costs
    the same on a month of readings as on a day; only a key whose values
    stray outside its range is counted reading by reading.
    """
    from decoders.quality import quality_registry

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    totals = repo.measurement_totals(vehicle_id, since)
    live = [t for t in totals if t["kind"] == "live"]
    sess = sessions_from_minutes(repo.activity_minutes(vehicle_id, since), gap_s)
    n_reads = sum(t["n"] for t in live)
    n_ok = sum(t["ok"] for t in live)
    by_prov = {kind: sum(t["ok"] for t in totals if t["kind"] == kind)
               for kind in ("live", "imported", "simulated")}
    last_ts = max((t["last_ok"] for t in live if t["last_ok"]), default=None)

    ranges: dict[str, tuple] = {}
    for t in live:
        if not t["ok"]:
            continue
        lo, hi = ranges.get(t["key"], (None, None))
        vals = [v for v in (lo, t["vmin"]) if v is not None]
        lo = min(vals) if vals else None
        vals = [v for v in (hi, t["vmax"]) if v is not None]
        hi = max(vals) if vals else None
        ranges[t["key"]] = (lo, hi)
    keys = sorted(ranges)

    registry = quality_registry(profile)
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
        seen_lo, seen_hi = ranges[key]
        if lo is None or hi is None or seen_lo is None \
                or (seen_lo >= lo and seen_hi <= hi):
            continue
        n = repo.conn.execute(
            "SELECT COUNT(*) FROM measurements WHERE vehicle_id=? AND key=? "
            "AND ts>=? AND success=1 AND provenance NOT LIKE ? "
            "AND provenance <> 'simulated' AND typeof(value) IN "
            "('integer','real') AND (value < ? OR value > ?)",
            (vehicle_id, key, since, IMPORTED_PREFIX + "%", lo, hi)).fetchone()[0]
        if n:
            implausible.append({"key": key, "count": n, "range": [lo, hi]})
    implausible.sort(key=lambda r: -r["count"])

    return {
        "days": days,
        "last_ts": last_ts,
        "sessions": len(sess),
        "latest_session": sess[-1] if sess else None,
        "reads": n_reads,
        "reads_ok": n_ok,
        "success_pct": round(100.0 * n_ok / n_reads, 1) if n_reads else None,
        "live_rows": by_prov["live"],
        "imported_rows": by_prov["imported"],
        "simulated_rows": by_prov["simulated"],
        "signals": len(keys),
        "evidence": evidence,
        "uncatalogued": uncatalogued,
        "implausible": implausible,
        "implausible_rows": sum(r["count"] for r in implausible),
        "retired_dids": len(repo.unsupported_dids(vehicle_id)),
    }
