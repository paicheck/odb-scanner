"""Anomaly detection: outliers against a trailing baseline.

Two families of detector live here deliberately.

`detect_series_anomalies` / `scan_metric` are the original whole-window z-score
test. They are kept because they are transparent and are referenced by the CLI
and the existing tests, but a single mean/std over the whole window is the wrong
baseline for a car (a slow real drift hides inside its own baseline, and one
bad row inflates the spread). See the module docstring in
`analysis/baseline.py` for the full reasoning.

`scan_robust` is the layered detector the investigation platform uses: robust
median/MAD statistics computed *inside* gap-free segments, plus a
rate-of-change layer, gated by `decoders/quality.py` so a key whose decoder is
known wrong can never produce a finding. A known-invalid key excluded here
remains fully visible in `analysis/decoder_lab.py`, which is where it belongs.
"""
from __future__ import annotations

from analysis import baseline as baseline_mod
from analysis import stats
from decoders.quality import DataKind, quality_for, quality_registry

# A scan only makes sense on a single real number. Text keys (gear, VIN) and
# JSON keys share the `value` column but hold strings, and feeding one to the
# numeric detectors raises `float('P')`. Derived values are still numbers, so
# they are scanable; everything else is refused.
_SCANABLE_KINDS = (DataKind.NUMERIC, DataKind.DERIVED)


def _as_float(value):
    """A stored value as a float, or None if it is not a single number.

    `value` has no declared type in SQLite, so a text decode lands there
    verbatim; a scan must drop those rather than crash on them.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_series_anomalies(ts_list: list[str], values: list[float],
                            z_threshold: float = 3.0):
    """Return (ts, value, z) tuples whose z-score exceeds the threshold.

    Baseline uses ALL samples in the series (simple, transparent approach;
    documented as such — not a sophisticated online detector).
    """
    mean = stats.mean(values)
    std = stats.stdev(values)
    if mean is None or std in (None, 0):
        return []
    out = []
    for ts, v in zip(ts_list, values):
        z = stats.zscore(v, mean, std)
        if z is not None and abs(z) >= z_threshold:
            out.append((ts, v, round(z, 2)))
    return out


def scan_metric(repo, vehicle_id: int, metric: str, description: str,
                days: int = 30, z_threshold: float = 3.0,
                battery_field: bool = True) -> int:
    """Detect and persist anomalies for one metric. Returns count found."""
    hist = repo.battery_history(days, vehicle_id)
    if battery_field:
        ts_list = [r["ts"] for r in hist if r[metric] is not None]
        values = [r[metric] for r in hist if r[metric] is not None]
    else:
        rows = repo.measurement_series(vehicle_id, metric, days)
        ts_list = [r["ts"] for r in rows]
        values = [r["value"] for r in rows]
    found = detect_series_anomalies(ts_list, values, z_threshold)
    for ts, v, z in found:
        repo.add_anomaly(
            vehicle_id, ts, metric, v,
            stats.mean(values), stats.stdev(values), z,
            "high" if z > 0 else "low", description,
        )
    return len(found)


# Battery snapshot columns -> the measurement key they are derived from, so the
# quality gate can be applied to the derived column too. Anything not listed is
# allowed (the column is built from several keys and has no single decoder).
_BATTERY_FIELD_TO_KEY = {
    "soc_abs_pct": "soc_abs",
    "soc_normal_pct": "soc_normal",
    "pack_voltage_v": "pack_voltage",
    "pack_current_a": "pack_current",
    "pack_power_kw": "pack_power_kw",
    "cell_min_v": "cell_voltage_min",
    "cell_max_v": "cell_voltage_max",
    "cell_delta_mv": "cell_delta_mv",
    "battery_temp_c": "battery_temp",
    "soh_pct": "soh_pct",
}


def scan_series_robust(ts_list: list[str], values: list[float], *,
                       z_threshold: float = baseline_mod.DEFAULT_Z,
                       max_gap_s: float = baseline_mod.DEFAULT_MAX_GAP_S
                       ) -> list[baseline_mod.Finding]:
    """Layered detection on an in-memory series. Pure, so it is testable and
    the caller decides whether to persist the findings.

    The two layers are merged by timestamp, preferring the robust-z finding
    when both fire on the same sample -- a value far from the median is the
    more useful description than its step size.
    """
    _, by_value = baseline_mod.detect_outliers(
        ts_list, values, max_gap_s=max_gap_s, z_threshold=z_threshold)
    by_rate = baseline_mod.detect_rate_of_change(
        ts_list, values, max_gap_s=max_gap_s, z_threshold=z_threshold)
    merged: dict[str, baseline_mod.Finding] = {}
    for f in by_rate:
        merged[f.ts] = f
    for f in by_value:
        merged[f.ts] = f
    return sorted(merged.values(), key=lambda f: f.ts)


def scan_robust(repo, vehicle_id: int, metric: str, description: str,
                days: int = 30, *, profile: str = "meb",
                battery_field: bool = False,
                z_threshold: float = baseline_mod.DEFAULT_Z,
                persist: bool = True) -> list[baseline_mod.Finding]:
    """Robust scan of one metric, gated by the quality registry.

    Returns the findings (also persisted when `persist`). A metric whose
    decoder is known invalid returns [] and records nothing: scoring it would
    make the vehicle's most alarming numbers the ones the decoder got wrong.
    A key that is not a single number (text or JSON) likewise returns [], and
    individual non-numeric rows inside a series are dropped rather than
    crashing the detector.
    """
    key = metric if not battery_field else _BATTERY_FIELD_TO_KEY.get(metric,
                                                                    metric)
    q = quality_for(profile, key)
    if not q.use_in_scoring:
        return []
    if not battery_field and q.data_kind not in _SCANABLE_KINDS:
        return []
    if battery_field:
        rows = repo.battery_history(days, vehicle_id)
        pairs = [(r["ts"], v) for r in rows
                 if (v := _as_float(r[metric])) is not None]
    else:
        rows = repo.measurement_series(vehicle_id, metric, days)
        pairs = [(r["ts"], v) for r in rows
                 if (v := _as_float(r["value"])) is not None]
    ts_list = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    findings = scan_series_robust(ts_list, values, z_threshold=z_threshold)
    if persist:
        for f in findings:
            repo.add_anomaly(
                vehicle_id, f.ts, metric, f.value,
                f.baseline.get("median"), f.baseline.get("sigma"), f.score,
                f.direction, f"{description} [{f.method}] {f.reason}")
    return findings


def scan_all_robust(repo, vehicle_id: int, keys: list[str], *,
                    days: int = 30, profile: str = "meb",
                    z_threshold: float = baseline_mod.DEFAULT_Z,
                    persist: bool = True) -> dict:
    """Scan many measurement keys; report counts and which keys were skipped so
    the omission is explicit rather than silent.

    Skipped keys are split by reason: `skipped` (a known-invalid decoder),
    `unknown` (a key with no catalogue definition) and `not_numeric` (text or
    JSON, not a single number). Set `persist=False` for a dry run that writes
    nothing.
    """
    registry = quality_registry(profile)
    out = {"scanned": 0, "findings": 0, "skipped": [], "unknown": [],
           "not_numeric": []}
    for key in keys:
        q = registry.get(key)
        if q is None:
            out["unknown"].append(key)
            continue
        if not q.use_in_scoring:
            out["skipped"].append(key)
            continue
        if q.data_kind not in _SCANABLE_KINDS:
            out["not_numeric"].append(key)
            continue
        found = scan_robust(repo, vehicle_id, key, key, days, profile=profile,
                            z_threshold=z_threshold, persist=persist)
        out["scanned"] += 1
        out["findings"] += len(found)
    return out
