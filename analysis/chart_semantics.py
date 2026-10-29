"""Chart semantics: units, axis policies, precision and series annotation.

WHY THIS EXISTS
---------------
Every chart used to decide its own axis, precision and labels in JavaScript,
from a single generic number formatter. That produced the problems recorded in
docs/investigation/chart_semantics/progress.md: absolute and normalised SoC
merged into one line, `degC` printed raw, a state-of-charge axis that could
wander outside 0-100 %, signed current with no zero reference, summary
statistics computed on downsampled points, and no sign of where a value came
from.

This module is the one place those decisions are made, in Python, so they can
be tested without a browser. The browser only draws what it is told.

Rules it enforces:

  * units come from the measurement catalogue (`decoders/quality.py`); a stored
    unit is only a fallback, and a disagreement is reported, never resolved by
    guessing;
  * a chart axis holds exactly one unit -- incompatible units go to separate
    panels (a second, labelled axis only where a page explicitly asks for it);
  * nothing is interpolated, smoothed, re-stamped or deleted: statistics are
    computed on the raw values, out-of-range values are *flagged* and kept;
  * an empty series and a series of genuine zeroes stay distinguishable.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

# Display spelling only. The stored/catalogue unit string is never changed.
_UNIT_LABELS = {
    "degC": "°C",
    "degF": "°F",
    "kWh/100km": "kWh/100 km",
    "code": "",
}


def unit_label(unit: str | None) -> str:
    """Human spelling of a catalogue unit (``degC`` -> ``°C``)."""
    if not unit:
        return ""
    return _UNIT_LABELS.get(unit, unit)


# Decimal places that are honest for a unit. Chosen from the resolution the
# decoders actually produce (e.g. pack voltage in 0.25 V steps, cell voltage in
# ~0.25 mV steps); never more digits than the source carries.
_UNIT_DECIMALS = {
    "%": 1, "V": 1, "mV": 1, "A": 1, "kW": 2, "W": 0, "kWh": 2, "Wh": 0,
    "km": 0, "km/h": 0, "miles": 0, "degC": 1, "kWh/100km": 1, "Ah": 1,
    "bar": 2, "Nm": 1, "rpm": 0, "mOhm": 1,
}
_KEY_DECIMALS = {
    "cell_voltage_max": 3, "cell_voltage_min": 3,
    "lv_voltage_obd": 2, "aux_12v_voltage": 2,
    "dcdc_voltage": 2, "dcdc_gw_voltage": 2,
}


def decimals_for(key: str, unit: str | None) -> int:
    if key in _KEY_DECIMALS:
        return _KEY_DECIMALS[key]
    if key.startswith("cell_v_"):
        return 3
    return _UNIT_DECIMALS.get(unit or "", 2)


# ---------------------------------------------------------------------------
# Scale policies
# ---------------------------------------------------------------------------

BOUNDED = "bounded"        # physical limits (SoC 0-100 %); auto-fit inside them
SIGNED = "signed"          # sign is the meaning (current, power): include zero
CUMULATIVE = "cumulative"  # running counters: fit the data, never force zero
ZERO_BASED = "zero-based"  # non-negative magnitudes where 0 is a real reference
AUTO = "auto"              # fit the data (voltages, temperatures)

POLICY_NOTES = {
    BOUNDED: "auto-fit within the valid {lo:g}–{hi:g} {unit} range",
    SIGNED: "includes zero; negative and positive values are both meaningful",
    CUMULATIVE: "running counter: axis fitted to the data, does not start at zero",
    ZERO_BASED: "axis starts at zero",
    AUTO: "axis fitted to the data, does not start at zero",
}

_UNIT_POLICY: dict[str, tuple[str, tuple[float, float] | None]] = {
    "%": (BOUNDED, (0.0, 100.0)),
    "A": (SIGNED, None),
    "kW": (SIGNED, None),
    "W": (SIGNED, None),
    "mV": (ZERO_BASED, None),
    "km": (ZERO_BASED, None),
    "miles": (ZERO_BASED, None),
    "Wh": (ZERO_BASED, None),
    "kWh/100km": (ZERO_BASED, None),
    "km/h": (ZERO_BASED, None),
    "Ah": (CUMULATIVE, None),
    "kWh": (CUMULATIVE, None),
}

# Counters measured in a unit that is otherwise a magnitude.
_CUMULATIVE_KEYS = {
    "odometer_km", "odometer_bms", "odometer_2", "eldrive_odometer",
    "total_distance_travelled_lifetime",
}


def policy_for(key: str, unit: str | None) -> tuple[str, tuple[float, float] | None]:
    if key in _CUMULATIVE_KEYS:
        return CUMULATIVE, None
    return _UNIT_POLICY.get(unit or "", (AUTO, None))


def _nice_step(span: float) -> float:
    """A 1-2-5 step giving roughly five intervals over `span`."""
    raw = span / 5.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag  # pragma: no cover - loop always returns


def axis_range(values, policy: str, bounds=None) -> dict | None:
    """Axis bounds for the finite values under a policy, or None when empty.

    Returns ``{"min", "max", "step", "tick_decimals"}`` rounded outward to a
    1-2-5 step so ticks land on readable numbers. A constant series gets a
    visible band around its value instead of a zero-height axis.
    """
    finite = [float(v) for v in values
              if isinstance(v, (int, float)) and not isinstance(v, bool)
              and math.isfinite(v)]
    if not finite:
        return None
    lo, hi = min(finite), max(finite)
    if policy in (ZERO_BASED, SIGNED):
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi == lo:
        pad = abs(hi) * 0.05 or 1.0
        if policy == ZERO_BASED and lo == 0.0:
            lo, hi = 0.0, pad
        elif policy == SIGNED and lo == 0.0:
            lo, hi = -pad, pad
        else:
            lo, hi = lo - pad, hi + pad
    step = _nice_step(hi - lo)
    out_lo = math.floor(lo / step) * step
    out_hi = math.ceil(hi / step) * step
    if policy == ZERO_BASED and min(finite) >= 0:
        out_lo = 0.0
    if bounds is not None:
        out_lo = max(out_lo, bounds[0])
        out_hi = min(out_hi, bounds[1])
    tick_decimals = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    return {"min": round(out_lo, 10), "max": round(out_hi, 10),
            "step": step, "tick_decimals": tick_decimals}


def axis_for(key: str, unit: str | None, values=()) -> dict:
    """Everything the browser needs to draw one y-axis for this key."""
    policy, bounds = policy_for(key, unit)
    label = unit_label(unit)
    note = POLICY_NOTES[policy]
    if policy == BOUNDED and bounds:
        note = note.format(lo=bounds[0], hi=bounds[1], unit=label)
    return {
        "unit": unit or "",
        "unit_label": label,
        "group": unit or "",
        "policy": policy,
        "bounds": list(bounds) if bounds else None,
        "decimals": decimals_for(key, unit),
        "note": note,
        "range": axis_range(values, policy, bounds),
    }


# ---------------------------------------------------------------------------
# Statistics, plausibility, coverage
# ---------------------------------------------------------------------------

def _is_num(v) -> bool:
    t = type(v)
    if t is float or t is int:            # every value SQLite hands back
        return math.isfinite(v)
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def raw_stats(values) -> dict:
    """n / min / max / mean / latest over the RAW values, never the sampled ones.

    ``n`` counts numeric readings; ``non_numeric`` counts rows that are not a
    number (text, NULL). An empty series has n=0 and None statistics, which is
    how "no data" stays distinct from "a reading of 0".
    """
    nums = [float(v) for v in values if _is_num(v)]
    out = {"n": len(nums), "non_numeric": len(list(values)) - len(nums),
           "min": None, "max": None, "mean": None, "latest": None}
    if nums:
        out.update(min=min(nums), max=max(nums),
                   mean=sum(nums) / len(nums), latest=nums[-1])
    return out


def plausibility(ts_list, values, quality) -> dict:
    """Which raw values fall outside the catalogue's documented range.

    Nothing is removed. A key without a documented range reports
    ``range: None`` instead of inventing one.
    """
    pmin, pmax = quality.plausible_min, quality.plausible_max
    out = {"range": None if pmin is None or pmax is None else [pmin, pmax],
           "basis": quality.plausible_basis, "known_issue": quality.known_issue,
           "outside": 0, "examples": []}
    if out["range"] is None:
        return out
    for ts, v in zip(ts_list, values):
        if _is_num(v) and (v < pmin or v > pmax):
            out["outside"] += 1
            if len(out["examples"]) < 10:
                out["examples"].append({"ts": ts, "value": v})
    return out


def _parse(ts):
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def coverage(ts_list, gap_s: float) -> dict:
    """First/last observation and the recording gaps wider than ``gap_s``."""
    times = [t for t in (_parse(ts) for ts in ts_list) if t is not None]
    out = {"first_ts": ts_list[0] if ts_list else None,
           "last_ts": ts_list[-1] if ts_list else None,
           "gap_threshold_s": gap_s, "gaps": 0, "largest_gap_s": None,
           "duplicate_timestamps": len(ts_list) - len(set(ts_list))}
    largest = None
    for a, b in zip(times, times[1:]):
        d = (b - a).total_seconds()
        if largest is None or d > largest:
            largest = d
        if gap_s and d > gap_s:
            out["gaps"] += 1
    out["largest_gap_s"] = largest
    return out


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def provenance_kind(provenance: str | None) -> str:
    """Coarse kind for a stored provenance string.

    reported / calculated / estimated / imported / simulated / unknown -- the
    vocabulary `database/observations_schema.sql` already uses.
    """
    p = (provenance or "").lower()
    if p.startswith("imported"):
        return "imported"
    if p in ("reported", "calculated", "estimated", "simulated"):
        return p
    return "unknown"


# ---------------------------------------------------------------------------
# Series assembly
# ---------------------------------------------------------------------------

def build_series(key: str, rows, *, name: str, unit: str, quality,
                 gap_s: float, max_points: int = 1500,
                 method: str = "min_max", unit_source: str = "catalogue",
                 unit_conflict=None) -> dict:
    """Annotated chart payload for one numeric key.

    ``rows`` are ``(ts, value, provenance)`` tuples in observation order. The
    returned dict keeps the legacy series keys (``ts``, ``value``,
    ``raw_points``...) so existing consumers keep working, and adds
    ``axis``, ``stats``, ``plausibility``, ``coverage`` and per-point
    ``provenance`` / ``out_of_range``.
    """
    from analysis import series as series_mod

    ts_list = [r[0] for r in rows]
    values = [r[1] for r in rows]
    provs = [provenance_kind(r[2]) for r in rows]
    payload = series_mod.numeric_payload(ts_list, values, max_points=max_points,
                                         method=method, provenance=provs)
    numeric = [i for i, v in enumerate(values) if _is_num(v)]
    num_ts = [ts_list[i] for i in numeric]
    nums = [values[i] for i in numeric]

    pmin, pmax = quality.plausible_min, quality.plausible_max
    payload["out_of_range"] = [
        pmin is not None and pmax is not None and (v < pmin or v > pmax)
        for v in payload["value"]]

    counts: dict[str, int] = {}
    for i in numeric:
        counts[provs[i]] = counts.get(provs[i], 0) + 1
    payload.update({
        "key": key,
        "name": name,
        "unit": unit,
        "unit_label": unit_label(unit),
        "unit_source": unit_source,
        "unit_conflict": list(unit_conflict or []),
        "data_kind": quality.data_kind.value,
        "verification": quality.verification.value,
        "derived": quality.data_kind.value == "derived",
        "axis": axis_for(key, unit, nums),
        "stats": raw_stats(values),
        "plausibility": plausibility(num_ts, nums, quality),
        "coverage": coverage(num_ts, gap_s),
        "provenance_counts": counts,
    })
    return payload


def assemble_panels(defs: list[dict], series: dict[str, dict]) -> list[dict]:
    """Turn page panel definitions into drawable panels, enforcing one unit per
    axis.

    A definition is ``{"id", "title", "question", "keys", "note"?,
    "dual"?}``. Keys whose units differ are split into separate panels
    (title suffixed with the unit), unless ``dual`` is set and there are
    exactly two units -- then the second unit goes on a labelled right axis.
    A panel whose series all have no readings is kept, flagged ``empty``.
    """
    out: list[dict] = []
    for d in defs:
        groups: dict[str, list[str]] = {}
        for k in d["keys"]:
            s = series.get(k)
            if s is None:
                continue
            groups.setdefault(s["axis"]["group"], []).append(k)
        if not groups:
            continue
        units = list(groups)
        if d.get("dual") and len(units) == 2:
            parts = [(d["id"], d["title"], groups[units[0]], groups[units[1]])]
        elif len(units) == 1:
            parts = [(d["id"], d["title"], groups[units[0]], [])]
        else:
            parts = [(f"{d['id']}-{i}",
                      f"{d['title']} ({unit_label(u) or 'no unit'})", groups[u], [])
                     for i, u in enumerate(units)]
        for pid, title, left, right in parts:
            keys = left + right
            out.append({
                "id": pid, "title": title,
                "question": d.get("question", ""), "note": d.get("note", ""),
                "left": left, "right": right,
                "left_axis": panel_axis(left, series),
                "right_axis": panel_axis(right, series),
                "empty": all(series[k]["stats"]["n"] == 0 for k in keys),
            })
    return out


def panel_axis(keys: list[str], series: dict[str, dict]) -> dict | None:
    """One axis covering every series on one side of a panel.

    The range is computed from all series together (their raw min/max), so
    hiding or showing a series does not make the axis jump.
    """
    if not keys:
        return None
    first = series[keys[0]]
    values = []
    for k in keys:
        st = series[k]["stats"]
        values += [v for v in (st["min"], st["max"]) if v is not None]
    return axis_for(keys[0], first["unit"], values)


def shared_x_range(series: dict[str, dict]) -> list[str] | None:
    """Earliest and latest observation across all series, so related panels
    share one time axis."""
    firsts = [s["coverage"]["first_ts"] for s in series.values()
              if s["coverage"]["first_ts"]]
    lasts = [s["coverage"]["last_ts"] for s in series.values()
             if s["coverage"]["last_ts"]]
    if not firsts:
        return None
    return [min(firsts, key=_parse_key), max(lasts, key=_parse_key)]


def _parse_key(ts):
    dt = _parse(ts)
    return dt or datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Pairing and derivations
# ---------------------------------------------------------------------------

def pair_nearest(a_rows, b_rows, tolerance_s: float) -> list[dict]:
    """Pair each A reading with the nearest B reading within ``tolerance_s``.

    Used to compare two independently sampled signals (e.g. range estimate vs
    SoC) without fabricating simultaneous readings: each pair keeps both real
    timestamps and the offset between them. A reading with no partner inside
    the tolerance is dropped from the comparison, not interpolated.
    """
    b = [(t, _parse(t), v) for t, v in b_rows if _is_num(v) and _parse(t)]
    b.sort(key=lambda r: r[1])
    out = []
    j = 0
    for ta, va in a_rows:
        da = _parse(ta)
        if da is None or not _is_num(va) or not b:
            continue
        while j + 1 < len(b) and b[j + 1][1] <= da:
            j += 1
        best = None
        for cand in b[max(0, j - 1):j + 2]:
            d = abs((cand[1] - da).total_seconds())
            if best is None or d < best[0]:
                best = (d, cand)
        if best and best[0] <= tolerance_s:
            out.append({"x": va, "y": best[1][2], "ts_a": ta,
                        "ts_b": best[1][0],
                        "dt_s": round((best[1][1] - da).total_seconds(), 1)})
    return out


def thin_pairs(pairs: list[dict], limit: int) -> list[dict]:
    """At most about `limit` of `pairs`, evenly spread through them, keeping
    the lowest and highest x and y so the axes and the outliers stay put.

    A month of a fast-polled signal pairs into tens of thousands of points:
    megabytes in the page, and more than a scatter can show apart.
    """
    if limit <= 0 or len(pairs) <= limit:
        return list(pairs)
    keep = {round(i * (len(pairs) - 1) / (limit - 1)) for i in range(limit)}
    for axis in ("x", "y"):
        keep.add(min(range(len(pairs)), key=lambda i: pairs[i][axis]))
        keep.add(max(range(len(pairs)), key=lambda i: pairs[i][axis]))
    return [pairs[i] for i in sorted(keep)]


def counter_delta(rows) -> dict:
    """Distance (or energy) between the first and last reading of a counter.

    Returns ``status``: ``ok``, ``insufficient`` (fewer than two readings) or
    ``inconsistent`` (the counter went backwards, so the difference would not
    be a distance). The result is a difference of two vehicle readings, not a
    trip log: it says nothing about how many journeys it spans.
    """
    pts = [(t, float(v)) for t, v in rows if _is_num(v)]
    if len(pts) < 2:
        return {"status": "insufficient", "delta": None, "n": len(pts),
                "first_ts": pts[0][0] if pts else None,
                "last_ts": pts[-1][0] if pts else None}
    if any(b[1] < a[1] for a, b in zip(pts, pts[1:])):
        return {"status": "inconsistent", "delta": None, "n": len(pts),
                "first_ts": pts[0][0], "last_ts": pts[-1][0]}
    return {"status": "ok", "delta": pts[-1][1] - pts[0][1], "n": len(pts),
            "first_ts": pts[0][0], "last_ts": pts[-1][0]}
