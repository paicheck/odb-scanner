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
from decoders.quality import use_in_scoring


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
    """
    key = metric if not battery_field else _BATTERY_FIELD_TO_KEY.get(metric,
                                                                    metric)
    if not use_in_scoring(profile, key):
        return []
    if battery_field:
        rows = repo.battery_history(days, vehicle_id)
        pairs = [(r["ts"], r[metric]) for r in rows if r[metric] is not None]
    else:
        rows = repo.measurement_series(vehicle_id, metric, days)
        pairs = [(r["ts"], r["value"]) for r in rows if r["value"] is not None]
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
                    z_threshold: float = baseline_mod.DEFAULT_Z) -> dict:
    """Scan many measurement keys; report counts and which keys were skipped as
    known-invalid so the omission is explicit rather than silent."""
    out = {"scanned": 0, "findings": 0, "skipped": []}
    for key in keys:
        if not use_in_scoring(profile, key):
            out["skipped"].append(key)
            continue
        found = scan_robust(repo, vehicle_id, key, key, days, profile=profile,
                            z_threshold=z_threshold)
        out["scanned"] += 1
        out["findings"] += len(found)
    return out
