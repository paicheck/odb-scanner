"""Arrival ranges from this car's physics model (analysis/energy_model.py).

The planner's own ranges (planner._add_bands with a RateError) take one
drive's per-km error and multiply it by the leg: a 200 km leg is given twenty
times a 10 km drive's error, odometer rounding and all. Here each leg's range
comes from the model's predictive spread for that leg instead:

  * the part of the error that persists over a journey (wind, rain, load,
    style) grows with the leg's length -- the wind part with speed², and
    less of it when the forecast wind is counted;
  * the part that averages out (traffic, lights) grows with √length;
  * the coefficients' own uncertainty, larger outside the conditions the car
    has been driven in;
  * a forecast temperature error, through the model's temperature terms;
  * one energy reading at each end.

That spread is widened by the calibration learned on the backtest's drives
(analysis/consumption_backtest.py `calibration`: how far off the model really
was, standardised, on drives it predicted before they happened), and the
quantile is Student's t for the number of drives that calibration rests on,
so few drives mean a wider range, not a confident one.

Legs are independent: each stop charges to a planned level, so one leg
running over does not carry into the next. (Trip totals are another matter:
the journey part of the error is shared across legs, so it would add up, not
average out.)

Every range says how confident it is and why, in plain words. Pure; no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt

from analysis.consumption_backtest import BAND_Q, MIN_PREDICTIONS
from analysis.energy_model import Conditions, PhysicsModel, t_quantile

# Short-range 2 m temperature forecasts are typically within 1-2 °C (rms) of
# what happens; the planner plans for the next hours.
FORECAST_TEMP_SD = 1.5
STALE_START_H = 6.0          # an older battery reading may no longer be true
GOOD_DRIVES = 40
GOOD_CALIBRATION = 30


@dataclass
class ModelRange:
    model: PhysicsModel
    usable_kwh: float
    calibration: float = 1.0
    calibration_drives: int = 0
    temp_c: float | None = None       # the route's (mean forecast) temperature
    speed_kmh: float | None = None    # the route's average speed
    temp_sd: float = 0.0              # how far that temperature may be off
    wind_share: float = 1.0           # FORECAST_WIND_SHARE with a wind forecast
    start_age_h: float | None = None  # age of the battery reading planned from
    start_sd_pct: float = 0.0
    q: float = BAND_Q[1]
    legs: list[float] = field(default_factory=list)

    def z(self) -> float:
        return t_quantile(self.q, max(3, self.calibration_drives)) * self.calibration

    def sd_pct(self, leg_km: float, first: bool) -> float:
        _, sd = self.model.leg(leg_km, Conditions.for_route(self.temp_c, self.speed_kmh),
                               self.temp_sd, self.wind_share)
        pct = sd / self.usable_kwh * 100.0
        return sqrt(pct ** 2 + self.start_sd_pct ** 2) if first else pct

    def band(self, pct: float, leg_km: float, first: bool) -> tuple[float, float]:
        """(low, high) % on arrival for a leg planned to arrive at `pct`."""
        self.legs.append(leg_km)
        half = self.z() * self.sd_pct(leg_km, first)
        return round(max(0.0, pct - half), 1), round(min(100.0, pct + half), 1)

    def checks(self) -> list[str]:
        """Why to trust the range less: each a plain sentence."""
        out = []
        m = self.model
        lo, hi = m.ranges.get("temp", (None, None))
        if self.temp_c is not None and lo is not None:
            if self.temp_c < lo - 3:
                out.append(f"Colder ({self.temp_c:.0f} °C) than any drive it learned "
                           f"from ({lo:.0f} to {hi:.0f} °C): heating there is from "
                           "physics, not measured.")
            elif self.temp_c > hi + 3:
                out.append(f"Warmer ({self.temp_c:.0f} °C) than any drive it learned "
                           f"from ({lo:.0f} to {hi:.0f} °C).")
        top = m.ranges.get("speed", (None, None))[1]
        if self.speed_kmh is not None and top is not None and self.speed_kmh > top + 10:
            out.append(f"Faster on average ({self.speed_kmh:.0f} km/h) than any drive it "
                       f"learned from (up to {top:.0f}): drag there is from physics, "
                       "not measured.")
        longest = m.ranges.get("km", (None, None))[1]
        if self.legs and longest and max(self.legs) > 2 * longest:
            out.append(f"Legs up to {max(self.legs):.0f} km, longer than any recorded "
                       f"drive ({longest:.0f} km): how the error grows over a whole "
                       "leg is assumed, not measured.")
        if self.start_age_h is not None and self.start_age_h > STALE_START_H:
            out.append(f"The battery reading planned from is {self.start_age_h:.0f} h "
                       "old; the car may have used some since.")
        if m.excluded:
            out.append(f"{m.excluded} drive(s) left out as implausible readings.")
        return out

    def confidence(self) -> str:
        checks = self.checks()
        if self.calibration_drives < MIN_PREDICTIONS or self.model.n_drives < 10:
            return "low"
        if any(c.startswith(("Colder", "Legs")) for c in checks):
            return "low"
        if (self.model.n_drives >= GOOD_DRIVES
                and self.calibration_drives >= GOOD_CALIBRATION and not checks):
            return "good"
        return "moderate"

    @property
    def basis(self) -> str:
        return (f"this car's physics model, from {self.model.n_drives} drives "
                f"({self.model.sample_km:.0f} km); widened x{self.calibration:.2f} as "
                f"its errors on {self.calibration_drives} drives it predicted "
                "beforehand require")

    def summary(self) -> dict:
        return {"method": "physics", "confidence": self.confidence(),
                "checks": self.checks(), "drives": self.model.n_drives,
                "km": round(self.model.sample_km), "calibration": self.calibration,
                "calibration_drives": self.calibration_drives,
                "z": round(self.z(), 2)}

    def assumption(self) -> str:
        m = self.model
        journey = sqrt(m.journey_var) * 100
        extra = []
        if self.temp_sd:
            extra.append(f"forecast temperature within ±{self.temp_sd:.1f} °C")
        if self.wind_share < 1:
            extra.append("the forecast wind's own error")
        return ("Arrival ranges: an 80 % range for each leg from " + self.basis
                + f". Journey-to-journey spread {journey:.1f} kWh/100 km plus wind "
                "growing with speed, a part that evens out over distance, and the "
                "model's own uncertainty" + (", " + ", ".join(extra) if extra else "")
                + f". Confidence: {self.confidence()}.")
