"""A physics-informed consumption model for this car, with honest uncertainty.

Where analysis/consumption_model.py fits straight lines in temperature and
average speed, this one fits the terms an electric car's energy is actually
made of. Each is linear in its coefficient, so the fit stays an ordinary
(weighted, Bayesian) least squares:

    kWh/km = base                                   rolling, drivetrain, stop-start
           + drag    x  mean(v²) / (100 km/h)² x rho/rho15   air drag
           + time    x  hours per km                 auxiliaries, kW
           + heat    x  max(0, 18 - T) x hours per km   heating, kW per °C
           + cold    x  max(0, 15 - T) / 10          a cold pack and cold tyres
           + warmup  x  max(0, 15 - T) / 10 / km     a cold start, once per drive

Heating and auxiliaries are power, so they cost more per km the slower the
drive; drag goes with the square of speed, so a drive half in town and half
on the motorway is judged by its mean of v², not its average speed.

Why a prior, and which. A car has tens of drives, not thousands, and town
drives dominate them. Each coefficient therefore starts from the ID.3's own
physics (PRIOR, with a deliberately wide spread) and the drives move it as far
as they justify. With few drives the model is the physics; with many it is
this car. It cannot overfit a handful of drives into a 5-coefficient fantasy,
and it does not forget that motorway drag exists because the last month was
town driving.

Uncertainty. Each drive's measured rate varies around the model's by
  * a part that persists over a journey (wind, rain, load, tyres, style) --
    the same per km however long the leg: variance `journey_var`;
  * a part that averages out with distance (traffic, lights): `local_var`/km;
  * the measurement: the odometer's whole kilometres (±0.41 km sd for a
    difference of two roundings) and the BMS's energy estimate
    (ENERGY_READ_SD per reading).
Both variances are estimated from the drives (shrunk toward a prior when
there are few), and the coefficients' own uncertainty is carried through.
A prediction for a 200 km leg is therefore wider than a 10 km drive's in
proportion to the correlated part only.

Bad drives (a mis-paired energy reading, a charge hidden in a gap) are
down-weighted by a Huber weight on their standardised residual, and a rate
no ID.3 can drive at is left out and counted.

Whether this model beats the flat rate and the linear model *on this car's
drives* is decided by analysis/consumption_backtest.py, chronologically.
Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt

from analysis.consumption_model import MIN_RATE_KM, _inverse

NAMES = ("base", "drag", "time", "heat", "cold", "warmup")
# ID.3 58 kWh: 1,800-2,000 kg with a driver, tyres of Crr 0.0075-0.0095,
# 85-92 % battery to wheels, so rolling is 0.040-0.061 kWh/km: the base, held
# close to that. Stop-start in town is time spent slowly, so it goes to
# `time` with the auxiliaries: 0.3-0.5 kW of those, and braking from town
# speeds 1-2 times a km is worth about as much again, so 0.6 kW with a spread
# wide enough for either; it fades on the motorway as it should. (If the base
# were free it would take the stop-start and charge it to the motorway.)
# Drag at 100 km/h: 0.5 x 1.225 x 0.63 x
# 27.8² / 0.88 N = 0.094 kWh/km. A resistive heater ~0.15 kW per degree below
# 18 °C; a cold pack ~1 % more per 10 °C below 15; warming a cabin from 5 °C
# ~0.4 kWh. Apart from the base, the spreads are wide on purpose: they keep
# the model physical, not right.
PRIOR = {"base": (0.052, 0.012), "drag": (0.094, 0.030), "time": (0.60, 0.60),
         "heat": (0.15, 0.12), "cold": (0.010, 0.020), "warmup": (0.40, 0.40)}
HEAT_REF_C = 18.0
COLD_REF_C = 15.0
RHO15 = 101325.0 / (287.05 * 288.15)
# Route speeds are averages over the road, not over a speed trace with its
# accelerations: mean(v²) is about this much more than average².
ROUTE_V2_RATIO = 1.2
ODOMETER_SD_KM = 0.41          # difference of two whole-km readings
ENERGY_READ_SD = 0.04          # kWh, one BMS energy-content reading
MIN_DRIVES = 3
RATE_RANGE = (0.04, 0.60)      # kWh/km: outside this a drive's figures are wrong
HUBER_K = 2.5
# Prior for the variance components (kWh/km)²: about 8 % of a typical rate
# persisting over a journey, and pseudo-drives that weigh it against the data.
JOURNEY_SD0 = 0.013
PRIOR_DRIVES = 5.0
# Wind changes drag by rho CdA v w: little in town, a lot at 110 km/h, and the
# same over a whole leg. With the wind along the road varying by ~3.5 m/s sd
# (10 m), 70 % of it at the car's height: (1.225 x 0.63 x 3.5 x 0.7 / 0.88 /
# 3.6 / 3600 x 100)² = 2.75e-4 (kWh/km)² per (100 km/h)². A forecast leaves
# its error, ~2 m/s: FORECAST_WIND_SHARE of that.
WIND_VAR0 = 2.75e-4
FORECAST_WIND_SHARE = (2.0 / 3.5) ** 2
# Hill energy in the targets, when a drive's net climb is known, is taken off
# with the planner's own figures (routing/planner.py CarModel), so the model
# learns the flat-road rate the planner adds hills to.
HILL_MASS_KG, HILL_EFF, HILL_REGEN = 1950.0, 0.90, 0.60


def _hill_kwh(climb_m: float) -> float:
    e = HILL_MASS_KG * 9.81 * climb_m / 3.6e6
    return e / HILL_EFF if e > 0 else e * HILL_REGEN


@dataclass
class Conditions:
    """What is known about a stretch of driving; None is unknown."""
    temp_c: float | None = None
    v2_kmh2: float | None = None       # mean of speed² over the distance
    hours_per_km: float | None = None
    km: float | None = None            # for the once-per-drive warm-up

    @classmethod
    def for_route(cls, temp_c=None, avg_speed_kmh=None, km=None) -> Conditions:
        """A planned stretch: the routing provider gives an average speed."""
        if avg_speed_kmh is None or avg_speed_kmh <= 0:
            return cls(temp_c, None, None, km)
        return cls(temp_c, avg_speed_kmh ** 2 * ROUTE_V2_RATIO, 1.0 / avg_speed_kmh, km)

    @classmethod
    def for_drive(cls, s) -> Conditions:
        """A recorded drive (analysis/consumption_model.DriveSample)."""
        hours = None
        if s.start_s is not None and s.end_s is not None and s.distance_km > 0:
            hours = max(0.0, s.end_s - s.start_s) / 3600.0 / s.distance_km
        v2 = s.v2_kmh2
        if v2 is None and s.avg_speed_kmh:
            v2 = s.avg_speed_kmh ** 2 * ROUTE_V2_RATIO
        return cls(s.temp_c, v2, hours, s.distance_km)


def features(c: Conditions) -> list[float | None]:
    """The model's x for these conditions; None where a condition is unknown."""
    t, v2, h = c.temp_c, c.v2_kmh2, c.hours_per_km
    rho = 1.0 if t is None else 101325.0 / (287.05 * (t + 273.15)) / RHO15
    drag = None if v2 is None else v2 / 1e4 * rho
    heat = None if (t is None or h is None) else max(0.0, HEAT_REF_C - t) * h
    cold = None if t is None else max(0.0, COLD_REF_C - t) / 10.0
    warm = None if t is None else (max(0.0, COLD_REF_C - t) / 10.0 / c.km
                                   if c.km else 0.0)
    return [1.0, drag, h, heat, cold, warm]


@dataclass
class PhysicsModel:
    mean: list[float]                    # coefficients, NAMES order
    cov: list[list[float]]               # their posterior covariance
    journey_var: float                   # (kWh/km)², persists over a leg
    local_var: float                     # (kWh/km)² x km, averages out
    x_mean: list[float]                  # distance-weighted feature means
    x_var: list[float]                   # and spreads, for unknown conditions
    wind_var: float = WIND_VAR0          # (kWh/km)² per (100 km/h)², over a leg
    n_drives: int = 0
    sample_km: float = 0.0
    excluded: int = 0                    # drives left out as implausible
    downweighted: int = 0                # drives the Huber weight reduced
    ranges: dict = field(default_factory=dict)   # "temp", "speed", "km": seen
    held: tuple[str, ...] = ()           # held at zero: the drives pushed them below
    kind: str = "physics"
    # For the planner's assumption text (routing/planner.py).
    features_used: tuple[str, ...] = ("temp", "speed")

    # -- prediction -----------------------------------------------------------
    def _x(self, c: Conditions) -> tuple[list[float], float]:
        """x with unknown parts at the training means, and the variance of the
        rate those unknowns leave (the conditions could be any seen)."""
        raw = features(c)
        x, unknown_var = [], 0.0
        for i, v in enumerate(raw):
            if v is None:
                x.append(self.x_mean[i])
                unknown_var += self.mean[i] ** 2 * self.x_var[i]
            else:
                x.append(v)
        return x, unknown_var

    def rate_sd(self, c: Conditions, wind_share: float = 1.0) -> tuple[float, float]:
        """(kWh/km, sd of the rate over a long leg): the model's own
        uncertainty, the journey's (its wind part scaled by `wind_share`: less
        when a wind forecast is counted), and that of anything not known."""
        x, unk = self._x(c)
        rate = sum(b * v for b, v in zip(self.mean, x))
        model_var = sum(x[a] * self.cov[a][b] * x[b]
                        for a in range(len(x)) for b in range(len(x)))
        u = (c.v2_kmh2 if c.v2_kmh2 is not None else self.x_mean[1] * 1e4) / 1e4
        journey = self.journey_var + wind_share * self.wind_var * u
        return max(0.03, rate), sqrt(max(0.0, model_var) + journey + unk)

    def coef_sd(self, c: Conditions) -> float:
        """The sd of the rate from the coefficients' own uncertainty alone:
        how well the drives pin down what the car uses in these conditions
        (not how much one journey varies)."""
        x, _ = self._x(c)
        return sqrt(max(0.0, sum(x[a] * self.cov[a][b] * x[b]
                                 for a in range(len(x)) for b in range(len(x)))))

    def learned(self, name: str) -> float:
        """How much the drives have narrowed one coefficient: 0 is the prior
        unchanged, 1 is pinned down exactly."""
        if name in self.held:
            return 0.0
        i = NAMES.index(name)
        return max(0.0, 1.0 - sqrt(max(0.0, self.cov[i][i])) / PRIOR[name][1])

    def leg(self, km: float, c: Conditions, temp_sd: float = 0.0,
            wind_share: float = 1.0) -> tuple[float, float]:
        """(kWh, sd kWh) for `km` of driving in conditions `c`. `temp_sd`: how
        far the temperature may be off (a forecast's error), °C; `wind_share`:
        see rate_sd. The journey part grows with km, the local part with √km;
        one energy reading at each end."""
        rate, sd = self.rate_sd(c, wind_share)
        var = (km * sd) ** 2 + km * self.local_var + 2 * ENERGY_READ_SD ** 2
        if temp_sd and c.temp_c is not None:
            up = self.rate_sd(Conditions(c.temp_c - temp_sd, c.v2_kmh2,
                                         c.hours_per_km, c.km))[0]
            var += (km * (up - rate)) ** 2
        return km * rate, sqrt(var)

    def drive(self, s) -> tuple[float, float]:
        """(kWh, sd kWh) a recorded drive should have measured, its hills
        included when its climb is known (for the backtest)."""
        c = Conditions.for_drive(s)
        kwh, sd = self.leg(s.distance_km, c)
        if s.net_climb_m is not None:
            kwh += _hill_kwh(s.net_climb_m)
        rate = kwh / s.distance_km if s.distance_km else 0.0
        return kwh, sqrt(sd ** 2 + (rate * ODOMETER_SD_KM) ** 2)

    # The planner's interface (routing/planner.py _model_rate): flat-road
    # kWh/km for a route's temperature and average speed; hills are the
    # planner's. Warm-up is left out: a route is not a cold-started drive.
    def predict(self, temp_c=None, avg_speed_kmh=None, elev_gain_m_per_km=None,
                hvac_on=None):
        rate, sd = self.rate_sd(Conditions.for_route(temp_c, avg_speed_kmh))
        return rate, max(0.03, rate - sd), rate + sd

    def rate(self, temp_c=None, avg_speed_kmh=None, elev_gain_m_per_km=None,
             hvac_on=None) -> float:
        return self.predict(temp_c, avg_speed_kmh)[0]

    @property
    def residual_std(self) -> float:
        return sqrt(self.journey_var)

    # -- storage --------------------------------------------------------------
    def as_dict(self) -> dict:
        return {"kind": self.kind, "mean": list(self.mean),
                "cov": [list(r) for r in self.cov], "journey_var": self.journey_var,
                "local_var": self.local_var, "wind_var": self.wind_var,
                "x_mean": list(self.x_mean),
                "x_var": list(self.x_var), "n_drives": self.n_drives,
                "sample_km": self.sample_km, "excluded": self.excluded,
                "downweighted": self.downweighted,
                "ranges": {k: list(v) for k, v in self.ranges.items()},
                "features_used": list(self.features_used), "held": list(self.held),
                "coefficients": dict(zip(NAMES, self.mean))}

    @classmethod
    def from_dict(cls, d: dict) -> PhysicsModel:
        return cls(mean=[float(v) for v in d["mean"]],
                   cov=[[float(v) for v in r] for r in d["cov"]],
                   journey_var=float(d["journey_var"]), local_var=float(d["local_var"]),
                   x_mean=[float(v) for v in d["x_mean"]],
                   x_var=[float(v) for v in d["x_var"]],
                   wind_var=float(d.get("wind_var", WIND_VAR0)),
                   n_drives=int(d.get("n_drives") or 0),
                   sample_km=float(d.get("sample_km") or 0.0),
                   excluded=int(d.get("excluded") or 0),
                   downweighted=int(d.get("downweighted") or 0),
                   ranges={k: (float(v[0]), float(v[1]))
                           for k, v in (d.get("ranges") or {}).items()},
                   features_used=tuple(d.get("features_used") or ("temp", "speed")),
                   held=tuple(d.get("held") or ()))


# -- fitting ------------------------------------------------------------------

def _rows(samples):
    """(x, rate, km, sample, u) for drives with every condition known and a
    rate of their own (u: mean v² in (100 km/h)², for the wind variance); and
    how many were left out as implausible."""
    rows, excluded = [], 0
    for s in samples:
        km = s.distance_km
        if s.energy_kwh is None or km is None or km < MIN_RATE_KM:
            continue
        c = Conditions.for_drive(s)
        x = features(c)
        if any(v is None for v in x):
            continue
        hill = _hill_kwh(s.net_climb_m) if s.net_climb_m is not None else 0.0
        e = s.energy_kwh - hill
        y = e / km
        if not RATE_RANGE[0] <= y <= RATE_RANGE[1]:
            excluded += 1
            continue
        rows.append((x, y, km, s, c.v2_kmh2 / 1e4))
    return rows, excluded


def _meas_var(y: float, km: float) -> float:
    """Measurement variance of a drive's rate: odometer and energy readings."""
    return (y * ODOMETER_SD_KM / km) ** 2 + 2 * (ENERGY_READ_SD / km) ** 2


HELD_SD = 1e-7               # a coefficient held at zero: this sure of it


def _posterior(rows, w, held=()):
    """Posterior mean and covariance of the coefficients, given per-drive
    precisions w (1 / variance of the rate); those named in `held` are held
    at zero (see _constrained)."""
    p = len(NAMES)
    prec = [[0.0] * p for _ in range(p)]
    rhs = [0.0] * p
    for i, n in enumerate(NAMES):
        m0, s0 = (0.0, HELD_SD) if n in held else PRIOR[n]
        prec[i][i] += 1.0 / s0 ** 2
        rhs[i] += m0 / s0 ** 2
    for (x, y, *_), wi in zip(rows, w):
        for a in range(p):
            wx = wi * x[a]
            rhs[a] += wx * y
            row = prec[a]
            for b in range(a, p):
                row[b] += wx * x[b]
    for a in range(p):
        for b in range(a):
            prec[a][b] = prec[b][a]
    cov = _inverse(prec)
    if cov is None:
        return None, None
    mean = [sum(cov[a][b] * rhs[b] for b in range(p)) for a in range(p)]
    return mean, cov


def fit(samples) -> PhysicsModel | None:
    """The model these drives support: None with fewer than MIN_DRIVES usable
    drives (the caller keeps its flat figure)."""
    rows, excluded = _rows(samples)
    if len(rows) < MIN_DRIVES:
        return None
    n = len(rows)
    jv, wv, lv = JOURNEY_SD0 ** 2, WIND_VAR0, 0.0
    huber = [1.0] * n
    mean = cov = None

    def var(y, km, u):
        return jv + wv * u + lv / km + _meas_var(y, km)

    held: tuple[str, ...] = ()
    for _ in range(4):
        w = [h / var(y, km, u) for h, (_, y, km, _, u) in zip(huber, rows)]
        mean, cov, held = _constrained(rows, w)
        if mean is None:
            return None
        res = [y - sum(b * v for b, v in zip(mean, x)) for x, y, *_ in rows]
        # Variance components: what is left of each squared residual after the
        # measurement, regressed on [1, u, 1/km] (journey, wind, local),
        # non-negative, with the priors as pseudo-drives.
        jv, wv, lv = _components([(e * e - _meas_var(y, km), u, 1.0 / km, h)
                                  for e, (_, y, km, _, u), h in zip(res, rows, huber)])
        huber = [min(1.0, HUBER_K / abs(e) * sqrt(var(y, km, u))) if e else 1.0
                 for e, (_, y, km, _, u) in zip(res, rows)]
    kms = [r[2] for r in rows]
    total = sum(kms)
    p = len(NAMES)
    x_mean = [sum(km * x[i] for x, _, km, *_ in rows) / total for i in range(p)]
    x_var = [sum(km * (x[i] - x_mean[i]) ** 2 for x, _, km, *_ in rows) / total
             for i in range(p)]
    temps = [r[3].temp_c for r in rows]
    speeds = [r[3].avg_speed_kmh for r in rows if r[3].avg_speed_kmh is not None]
    ranges = {"temp": (min(temps), max(temps)), "km": (min(kms), max(kms))}
    if speeds:
        ranges["speed"] = (min(speeds), max(speeds))
    return PhysicsModel(mean, cov, jv, lv, x_mean, x_var, wind_var=wv, n_drives=n,
                        sample_km=total, excluded=excluded,
                        downweighted=sum(1 for h in huber if h < 1.0), ranges=ranges,
                        held=held)


def _constrained(rows, w):
    """The posterior with every coefficient non-negative. Each term is energy
    a car spends (rolling, drag, auxiliaries, heating, a cold pack, warming a
    cabin); none can be negative, and a negative one would make winter or
    the motorway look cheaper than it is. So a coefficient the drives push
    below zero is held at zero and the rest fitted again, the most negative
    first, until none is."""
    held: list[str] = []
    while True:
        mean, cov = _posterior(rows, w, held)
        if mean is None:
            return None, None, ()
        neg = [(m, n) for m, n in zip(mean, NAMES) if m < 0 and n not in held]
        if not neg:
            return [max(0.0, m) for m in mean], cov, tuple(held)
        held.append(min(neg)[1])


# The prior's pseudo-drives for the variance components: (excess, u, 1/km).
_COMPONENT_PRIOR = ((JOURNEY_SD0 ** 2, 0.0, 0.0),
                    (JOURNEY_SD0 ** 2 + WIND_VAR0 * 1.3, 1.3, 0.0))


def _components(z) -> tuple[float, float, float]:
    """(journey_var, wind_var, local_var) from (excess squared residual, u,
    1/km, weight): weighted least squares on [1, u, 1/km], each kept
    non-negative (a negative one is dropped and the rest refitted), with
    the prior as PRIOR_DRIVES pseudo-drives at each of _COMPONENT_PRIOR."""
    pts = list(z) + [(y, u, k, PRIOR_DRIVES) for y, u, k in _COMPONENT_PRIOR]
    active = [0, 1, 2]
    while True:
        xs = [[(1.0, u, k)[i] for i in active] for _, u, k, _ in pts]
        m = len(active)
        a = [[sum(w * x[i] * x[j] for x, (_, _, _, w) in zip(xs, pts)) for j in range(m)]
             for i in range(m)]
        rhs = [sum(w * x[i] * y for x, (y, _, _, w) in zip(xs, pts)) for i in range(m)]
        inv = _inverse(a)
        beta = [0.0] * m if inv is None else \
            [sum(inv[i][j] * rhs[j] for j in range(m)) for i in range(m)]
        neg = [active[i] for i in range(m) if beta[i] < 0]
        if not neg or len(active) == 1:
            break
        active.remove(min(neg, key=lambda i: beta[active.index(i)]))
    out = [0.0, 0.0, 0.0]
    for i, b in zip(active, beta):
        out[i] = max(0.0, b)
    return max(out[0], 0.003 ** 2), out[1], out[2]


# -- intervals ----------------------------------------------------------------

def t_quantile(q: float, dof: float) -> float:
    """Student's t quantile (Cornish-Fisher from the normal one); dof >= 1."""
    z = normal_quantile(q)
    if dof > 1000:
        return z
    v = max(1.0, dof)
    return (z + (z ** 3 + z) / (4 * v)
            + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * v ** 2)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * v ** 3))


def normal_quantile(q: float) -> float:
    """Acklam's rational approximation (|error| < 1.2e-9)."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    from math import log
    if not 0.0 < q < 1.0:
        raise ValueError("q must be inside (0, 1)")
    if q < 0.02425:
        r = sqrt(-2 * log(q))
        return (((((c[0] * r + c[1]) * r + c[2]) * r + c[3]) * r + c[4]) * r + c[5]) / \
            ((((d[0] * r + d[1]) * r + d[2]) * r + d[3]) * r + 1)
    if q > 1 - 0.02425:
        return -normal_quantile(1 - q)
    r = q - 0.5
    s = r * r
    return (((((a[0] * s + a[1]) * s + a[2]) * s + a[3]) * s + a[4]) * s + a[5]) * r / \
        (((((b[0] * s + b[1]) * s + b[2]) * s + b[3]) * s + b[4]) * s + 1)
