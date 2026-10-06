"""Per-measurement provenance: is this live, imported, stale or unavailable?

The dashboard had no way to tell the reader where a number came from, so an
empty battery page and a page full of values recorded from a Car Scanner export
a year ago look identical. Both are "no live data", and only one of them is a
defect.

Five labels, modelled as four mutually exclusive states plus one flag. The
prompt for this work lists Live / Imported / Stale / Unavailable / Unverified as
five categories, but "unverified" is not on the same axis as the other four: a
value can be live *and* unverified, and forcing it into one column would have to
report either "live" (hiding that the scale factor is a guess) or "unverified"
(hiding that the reading is current). So `unverified` is a flag.

    live        read from the vehicle within stale_after_s
    imported    carried over from a Car Scanner export (provenance starts
                "imported"), regardless of age -- it is evidence, not a reading
    stale       was read from the vehicle, but not recently
    unavailable in the decode registry, never successfully read here

Nothing here infers a value or fills a gap. A missing measurement stays missing:
Phase 12 of the brief is explicit that zero must never stand in for unavailable,
and this module exists so that a gap is visible rather than papered over.
"""
from __future__ import annotations

from datetime import datetime, timezone

# How old a vehicle reading may be and still be called live. Deliberately
# generous: the collector's slow DIDs (cell voltages, energy content) only run
# every collector.slow_poll_interval, so a tighter bound would report healthy
# data as stale between polls.
DEFAULT_STALE_AFTER_S = 900.0

IMPORTED_PREFIX = "imported"

# Per-cell and per-sensor keys are rolled up: the registry carries 108 cell
# voltage specs, and listing them individually would bury the other rows.
_KEY_GROUPS = (("cell_v_", "cell_voltages"), ("cell_t_", "cell_temperatures"))


def _age_seconds(ts: str | None, now: datetime | None) -> float | None:
    """Age of an ISO-8601 stamp, or None if it cannot be read.

    A row whose timestamp cannot be parsed is treated as unknown age rather than
    fresh, so a corrupt stamp cannot make data look live.
    """
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(tz=timezone.utc)
    return (reference - parsed).total_seconds()


def _group_for(key: str) -> tuple[str, bool]:
    """(display key, is_rollup) for one registry key."""
    for prefix, group in _KEY_GROUPS:
        if key.startswith(prefix):
            return group, True
    return key, False


def key_states(repo, vehicle_id: int, registry, *,
               stale_after_s: float = DEFAULT_STALE_AFTER_S,
               now: datetime | None = None) -> list[dict]:
    """One row per measurement, with where its value came from.

    Rows are sorted so the measurements a reader checks first are at the top:
    the core battery quantities, then everything else alphabetically, with the
    two rolled-up per-cell groups last.

    `registry` is a decoders.registry.DIDRegistry. Only keys it knows about are
    listed -- a measurement row for a key the registry no longer defines would
    otherwise show up here with nothing to describe it.
    """
    latest = repo.latest_measurements(vehicle_id) if vehicle_id else {}

    rolled: dict[str, dict] = {}
    rows: list[dict] = []
    for spec in registry.all():
        key = spec.key
        record = latest.get(key)
        row = _row_for(spec, record, stale_after_s, now)
        display, is_rollup = _group_for(key)
        if not is_rollup:
            rows.append(row)
            continue
        bucket = rolled.setdefault(display, {
            "key": display, "unit": spec.unit, "name": spec.name,
            "state": "unavailable", "source": None, "ts": None, "age_s": None,
            "value": None, "unverified": True, "known": 0, "live": 0,
            "imported": 0, "stale": 0, "unavailable": 0,
        })
        bucket["known"] += 1
        # Counted per state, so a sweep that answers 100 of 108 slots cannot
        # present itself as complete. `unavailable` is the count of slots with
        # no successful read -- no separate `missing` key, which would only be
        # a second name for the same number.
        bucket[row["state"]] += 1
        # Keep the freshest sample as the representative value.
        if record and (bucket["ts"] is None
                       or (row["age_s"] or 0) < (bucket["age_s"] or 0)):
            bucket["value"] = row["value"]
            bucket["ts"] = row["ts"]
            bucket["age_s"] = row["age_s"]
            bucket["source"] = row["source"]
        bucket["unverified"] = bucket["unverified"] and row["unverified"]

    core = ("soc_abs", "soc_normal", "pack_voltage", "pack_current",
            "battery_temp", "pack_power_kw")
    def sort_key(row):
        try:
            return (0, core.index(row["key"]))
        except ValueError:
            return (1, row["key"])
    rows.sort(key=sort_key)
    return rows + [rolled[k] for k in sorted(rolled)]


def _row_for(spec, record, stale_after_s: float,
             now: datetime | None) -> dict:
    doc = str(getattr(spec.doc_status, "value", spec.doc_status) or "")
    row = {
        "key": spec.key,
        "name": spec.name,
        "unit": spec.unit,
        # A decoder that is still a reverse-engineered guess says so here
        # regardless of how current the reading is.
        "unverified": doc not in ("", "documented"),
        "source": None, "ts": None, "age_s": None, "value": None,
        "state": "unavailable",
    }
    if record is None:
        return row
    source = str(record.get("provenance") or "")
    age = _age_seconds(record.get("ts"), now)
    row["source"] = source
    row["ts"] = record.get("ts")
    row["age_s"] = age
    row["value"] = record.get("value")
    if source.lower().startswith(IMPORTED_PREFIX):
        # Imported evidence never becomes live and never goes stale: it is a
        # reading of the car taken by another tool, and its age relative to
        # "now" says nothing about whether it is trustworthy evidence.
        row["state"] = "imported"
    elif age is None or age > stale_after_s:
        row["state"] = "stale"
    else:
        row["state"] = "live"
    return row


def summary(rows: list[dict]) -> dict:
    """Counts per state, for the dashboard's one-line overview."""
    out = {"live": 0, "imported": 0, "stale": 0, "unavailable": 0,
           "unverified": 0, "total": len(rows)}
    for row in rows:
        out[row["state"]] += 1
        if row["unverified"]:
            out["unverified"] += 1
    return out
