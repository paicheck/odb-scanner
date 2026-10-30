"""Personalised consumption model for the ID.3.

Learns how this car's energy use depends on temperature, speed, elevation and
HVAC from its own recorded drives, and predicts consumption for a planned route.
Pure functions, no I/O. Linear in features, weighted by drive distance.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

MIN_SAMPLE_KM = 50.0
MIN_DRIVES = 3


@dataclass
class DriveSample:
    distance_km: float
    energy_kwh: float
    temp_c: float | None = None
    avg_speed_kmh: float | None = None
    elev_gain_m: float | None = None
    hvac_on: bool | None = None


@dataclass
class ConsumptionModel:
    base_kwh_per_km: float
    temp_coeff: float = 0.0
    speed_coeff: float = 0.0
    elev_coeff: float = 0.0
    hvac_coeff: float = 0.0
    residual_std: float = 0.0
    sample_km: float = 0.0
    n_drives: int = 0
    features_used: tuple[str, ...] = ()

    def predict(self, temp_c=None, avg_speed_kmh=None,
                elev_gain_m_per_km=None, hvac_on=None):
        x = self.base_kwh_per_km
        if temp_c is not None:
            x += self.temp_coeff * temp_c
        if avg_speed_kmh is not None:
            x += self.speed_coeff * avg_speed_kmh
        if elev_gain_m_per_km is not None:
            x += self.elev_coeff * elev_gain_m_per_km
        if hvac_on is not None and self.hvac_coeff:
            x += self.hvac_coeff if hvac_on else 0.0
        x = max(0.05, x)
        half = max(self.residual_std, 0.01)
        return x, max(0.05, x - half), x + half
def _features(sample: DriveSample):
    km = sample.distance_km
    if km < 5.0 or sample.energy_kwh is None:
        return None
    rate = sample.energy_kwh / km
    row = [1.0]
    names = []
    if sample.temp_c is not None:
        row.append(sample.temp_c)
        names.append("temp")
    if sample.avg_speed_kmh is not None:
        row.append(sample.avg_speed_kmh)
        names.append("speed")
    if sample.elev_gain_m is not None and km > 0:
        row.append(sample.elev_gain_m / km)
        names.append("elev")
    if sample.hvac_on is not None:
        row.append(1.0 if sample.hvac_on else 0.0)
        names.append("hvac")
    return row, rate, km, names


def estimate(samples):
    rows = []
    for s in samples:
        f = _features(s)
        if f is None:
            continue
        row, rate, km, names = f
        rows.append((row, rate, km, names))
    total_km = sum(r[2] for r in rows)
    if total_km < MIN_SAMPLE_KM or len(rows) < MIN_DRIVES:
        return None
    common = set(rows[0][3])
    for r in rows[1:]:
        common &= set(r[3])
    order = ["temp", "speed", "elev", "hvac"]
    chosen = [n for n in order if n in common]
    A = []
    y = []
    w = []
    for row, rate, km, names in rows:
        if not all(n in names for n in chosen):
            continue
        vals = {}
        for i, n in enumerate(names):
            vals[n] = row[i + 1]
        r = [1.0] + [vals[n] for n in chosen]
        A.append(r)
        y.append(rate)
        w.append(km)
    if not A or sum(w) < MIN_SAMPLE_KM:
        return None
    p = len(A[0])
    XtWX = [[0.0] * p for _ in range(p)]
    XtWy = [0.0] * p
    for i, row in enumerate(A):
        wi = w[i]
        for a in range(p):
            for b in range(p):
                XtWX[a][b] += wi * row[a] * row[b]
            XtWy[a] += wi * row[a] * y[i]
    M = [row[:] + [XtWy[i]] for i, row in enumerate(XtWX)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            return None
        M[col], M[pivot] = M[pivot], M[col]
        pv = M[col][col]
        for j in range(col, p + 1):
            M[col][j] /= pv
        for r in range(p):
            if r == col:
                continue
            factor = M[r][col]
            if factor != 0.0:
                for j in range(col, p + 1):
                    M[r][j] -= factor * M[col][j]
    beta = [M[i][p] for i in range(p)]
    resids = [y[i] - sum(beta[j] * A[i][j] for j in range(p)) for i in range(len(A))]
    residual_std = sqrt(sum(r * r for r in resids) / max(1, len(resids) - p))
    name_map = {"temp": "temp_coeff", "speed": "speed_coeff",
                "elev": "elev_coeff", "hvac": "hvac_coeff"}
    coeffs = {"base_kwh_per_km": beta[0],
              "temp_coeff": 0.0, "speed_coeff": 0.0,
              "elev_coeff": 0.0, "hvac_coeff": 0.0}
    for idx, n in enumerate(chosen):
        coeffs[name_map[n]] = beta[idx + 1]
    return ConsumptionModel(
        base_kwh_per_km=coeffs["base_kwh_per_km"],
        temp_coeff=coeffs["temp_coeff"],
        speed_coeff=coeffs["speed_coeff"],
        elev_coeff=coeffs["elev_coeff"],
        hvac_coeff=coeffs["hvac_coeff"],
        residual_std=residual_std,
        sample_km=total_km,
        n_drives=len(A),
        features_used=tuple(chosen),
    )

