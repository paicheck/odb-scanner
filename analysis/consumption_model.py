"""Personalised consumption model for the ID.3.

Learns how this car's energy use depends on outside temperature, speed,
heating and climbing from its own recorded drives, and predicts the rate for
a planned stretch. Pure functions, no I/O. Linear in features, weighted by
drive distance (a 40 km drive's rate is a better measurement than a 6 km
one's: the odometer counts whole kilometres).

How it keeps from fooling itself:

* Features are centred on the drives' distance-weighted average, so the base
  rate is the car's rate in its usual conditions, and a condition the caller
  does not know adds nothing: an unknown speed is "a usual speed", not 0 km/h.
* A feature is used only with DRIVES_PER_COEFF drives for every coefficient,
  with enough spread among them (MIN_SPREAD), and only if it lowers the
  leave-one-out error by MIN_GAIN on the same drives: a coefficient that fits
  the drives it learned from but predicts no better is left out.
* A feature some drives lack does not cost the others: a drive is used while
  it has every feature chosen so far, and a feature is tried only on the
  drives that have it.
* Predictions never extrapolate: each input is clamped to the range of the
  drives the model learned from.
* The spread is distance-weighted like the fit, with its degrees of freedom.

Whether the model beats the flat rate on this car is a separate question,
answered on its own drives in time order by analysis/consumption_backtest.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt

MIN_SAMPLE_KM = 50.0
MIN_DRIVES = 3              # for the base rate alone
MIN_RATE_KM = 5.0           # shorter drives' rates are odometer rounding
DRIVES_PER_COEFF = 5        # drives needed for each coefficient fitted
MIN_GAIN = 0.05             # a feature must cut the leave-one-out error by 5 %
# Tried in this order. "elev" is metres climbed per km.
FEATURES = ("temp", "speed", "hvac", "elev")
FEATURE_LABELS = {"temp": "outside temperature", "speed": "speed",
                  "hvac": "heating/air con", "elev": "climbing"}
# Less spread than this among the drives and a slope is noise.
MIN_SPREAD = {"temp": 3.0, "speed": 8.0, "hvac": 0.3, "elev": 2.0}
_COEFF = {"temp": "temp_coeff", "speed": "speed_coeff",
          "elev": "elev_coeff", "hvac": "hvac_coeff"}


@dataclass
class DriveSample:
    distance_km: float
    energy_kwh: float
    temp_c: float | None = None
    avg_speed_kmh: float | None = None
    elev_gain_m: float | None = None
    hvac_on: bool | None = None
    start_s: float | None = None     # epoch seconds; the backtest needs them
    end_s: float | None = None
    # Mean of speed squared over the distance, (km/h)²: what air drag goes
    # with. A drive half at 30 and half at 120 km/h has an average speed of 48
    # but drags like 87. From the speed samples: sum(v³) / sum(v).
    v2_kmh2: float | None = None
    net_climb_m: float | None = None  # height at the end less at the start, if known


@dataclass
class ConsumptionModel:
    base_kwh_per_km: float           # at the training drives' average conditions
    temp_coeff: float = 0.0
    speed_coeff: float = 0.0
    elev_coeff: float = 0.0
    hvac_coeff: float = 0.0
    residual_std: float = 0.0
    sample_km: float = 0.0
    n_drives: int = 0
    features_used: tuple[str, ...] = ()
    means: dict = field(default_factory=dict)    # feature -> training average
    ranges: dict = field(default_factory=dict)   # feature -> (lowest, highest) seen
    loo_rms: float | None = None     # leave-one-out error, kWh/km, distance-weighted

    def rate(self, temp_c=None, avg_speed_kmh=None, elev_gain_m_per_km=None,
             hvac_on=None) -> float:
        given = {"temp": temp_c, "speed": avg_speed_kmh,
                 "elev": elev_gain_m_per_km,
                 "hvac": None if hvac_on is None else (1.0 if hvac_on else 0.0)}
        x = self.base_kwh_per_km
        for name, value in given.items():
            coeff = getattr(self, _COEFF[name])
            if value is None or not coeff:
                continue
            lo, hi = self.ranges.get(name, (value, value))
            x += coeff * (min(hi, max(lo, float(value))) - self.means.get(name, 0.0))
        return max(0.05, x)

    def predict(self, temp_c=None, avg_speed_kmh=None,
                elev_gain_m_per_km=None, hvac_on=None):
        """(expected, low, high) kWh/km; low/high one residual spread away."""
        x = self.rate(temp_c, avg_speed_kmh, elev_gain_m_per_km, hvac_on)
        half = max(self.residual_std, 0.01)
        return x, max(0.05, x - half), x + half

    def as_dict(self) -> dict:
        return {
            "base_kwh_per_km": self.base_kwh_per_km,
            "temp_coeff": self.temp_coeff, "speed_coeff": self.speed_coeff,
            "elev_coeff": self.elev_coeff, "hvac_coeff": self.hvac_coeff,
            "residual_std": self.residual_std, "sample_km": self.sample_km,
            "n_drives": self.n_drives, "features_used": list(self.features_used),
            "means": dict(self.means),
            "ranges": {k: list(v) for k, v in self.ranges.items()},
            "loo_rms": self.loo_rms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConsumptionModel:
        return cls(
            base_kwh_per_km=float(d["base_kwh_per_km"]),
            **{k: float(d.get(k) or 0.0) for k in _COEFF.values()},
            residual_std=float(d.get("residual_std") or 0.0),
            sample_km=float(d.get("sample_km") or 0.0),
            n_drives=int(d.get("n_drives") or 0),
            features_used=tuple(d.get("features_used") or ()),
            means={k: float(v) for k, v in (d.get("means") or {}).items()},
            ranges={k: (float(v[0]), float(v[1]))
                    for k, v in (d.get("ranges") or {}).items()},
            loo_rms=d.get("loo_rms"),
        )


def _rows(samples) -> list[tuple[float, float, dict]]:
    """(rate kWh/km, km, {feature: value}) for each drive long enough to have
    a rate of its own."""
    out = []
    for s in samples:
        km = s.distance_km
        if s.energy_kwh is None or km is None or km < MIN_RATE_KM:
            continue
        feats = {}
        if s.temp_c is not None:
            feats["temp"] = float(s.temp_c)
        if s.avg_speed_kmh is not None:
            feats["speed"] = float(s.avg_speed_kmh)
        if s.hvac_on is not None:
            feats["hvac"] = 1.0 if s.hvac_on else 0.0
        if s.elev_gain_m is not None:
            feats["elev"] = float(s.elev_gain_m) / km
        out.append((s.energy_kwh / km, km, feats))
    return out


def _inverse(m: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan with partial pivoting; None when singular."""
    p = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(p)]
         for i, row in enumerate(m)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        a[col] = [v / pv for v in a[col]]
        for r in range(p):
            if r != col and a[r][col] != 0.0:
                f = a[r][col]
                a[r] = [v - f * c for v, c in zip(a[r], a[col])]
    return [row[p:] for row in a]


@dataclass
class _Fit:
    beta: list[float]
    means: dict
    ranges: dict
    residual_std: float
    loo_rms: float
    n: int
    km: float


def _fit(rows, chosen: list[str]) -> _Fit | None:
    """Weighted least squares of rate on the chosen features, centred; None
    when there are too few drives, too little spread, or the fit is singular."""
    use = [r for r in rows if all(n in r[2] for n in chosen)]
    n, p = len(use), len(chosen) + 1
    km = sum(w for _, w, _ in use)
    need = DRIVES_PER_COEFF * p if chosen else MIN_DRIVES
    if km < MIN_SAMPLE_KM or n < need:
        return None
    means = {f: sum(w * x[f] for _, w, x in use) / km for f in chosen}
    for f in chosen:
        spread = sqrt(sum(w * (x[f] - means[f]) ** 2 for _, w, x in use) / km)
        if spread < MIN_SPREAD[f]:
            return None
    xs = [[1.0] + [x[f] - means[f] for f in chosen] for _, _, x in use]
    xtwx = [[sum(r[1] * xi[a] * xi[b] for r, xi in zip(use, xs)) for b in range(p)]
            for a in range(p)]
    inv = _inverse(xtwx)
    if inv is None:
        return None
    xtwy = [sum(r[1] * xi[a] * r[0] for r, xi in zip(use, xs)) for a in range(p)]
    beta = [sum(inv[a][b] * xtwy[b] for b in range(p)) for a in range(p)]
    rss = loo = 0.0
    for (y, w, _), xi in zip(use, xs):
        e = y - sum(b * v for b, v in zip(beta, xi))
        h = w * sum(xi[a] * inv[a][b] * xi[b] for a in range(p) for b in range(p))
        if h >= 1.0 - 1e-9:
            return None                      # one drive decides a coefficient
        rss += w * e * e
        loo += w * (e / (1.0 - h)) ** 2
    ranges = {f: (min(x[f] for _, _, x in use), max(x[f] for _, _, x in use))
              for f in chosen}
    return _Fit(beta, means, ranges, sqrt(rss / km * n / (n - p)),
                sqrt(loo / km), n, km)


def estimate(samples) -> ConsumptionModel | None:
    """The model these drives support, or None when they do not support even
    a base rate (fewer than MIN_DRIVES drives or MIN_SAMPLE_KM km)."""
    rows = _rows(samples)
    chosen: list[str] = []
    best = _fit(rows, chosen)
    if best is None:
        return None
    for f in FEATURES:
        trial = _fit(rows, chosen + [f])
        if trial is None:
            continue
        # Judge the feature on the drives it would be fitted to.
        same = _fit([r for r in rows if f in r[2]], chosen)
        if same is None or trial.loo_rms >= same.loo_rms * (1.0 - MIN_GAIN):
            continue
        chosen, best = chosen + [f], trial
    coeffs = {_COEFF[f]: best.beta[i + 1] for i, f in enumerate(chosen)}
    return ConsumptionModel(
        base_kwh_per_km=best.beta[0], **coeffs,
        residual_std=best.residual_std, sample_km=best.km, n_drives=best.n,
        features_used=tuple(chosen), means=best.means, ranges=best.ranges,
        loo_rms=best.loo_rms)
