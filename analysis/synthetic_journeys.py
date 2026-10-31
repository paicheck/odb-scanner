"""Synthetic journeys with a known truth, for judging how well a consumption
prediction method works. NOT this car's data, and never written anywhere.

A year of driving by a hidden "truth" car, then measured the way this
collector measures the real one (analysis/trips.py, routing/car.py
drive_samples): the odometer in whole kilometres, the battery's energy
content in 25 Wh steps with the BMS's own estimation noise, speed sampled
every 5 s, the outside temperature as a sensor reads it. A method is only
any good if it recovers the truth through those measurements.

The truth is deliberately richer than any model judged on it, so no model
wins by being the generator: wind that changes with the day and reverses on
the way back, rain, payload, a battery that warms as it drives, a heater that
tops out, a warm-up that a short stop does not repeat, air conditioning in
summer, hills, and a driving style that varies from journey to journey.

Physics (per drive, at the battery):
  rolling   Crr m g d, Crr up in the cold and in the wet
  air       sum of 0.5 rho CdA (v + w)|v + w| v dt over the speed trace
  stops     each stop loses 0.5 m v² (1/eff - regen), v its road's cruise
  hills     m g dh / eff up, m g |dh| regen down
  time      (auxiliaries + heating or A/C) x driving time
  warm-up   a fixed energy per drive below 16 °C, unless parked < 30 min
  cold      drivetrain losses grow while the battery is below 18 °C

Pure functions of a seed; no I/O.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from analysis.consumption_model import DriveSample

DAY = 86400.0
G = 9.81
SAMPLE_S = 5.0
ENERGY_STEP_KWH = 0.025        # hv_energy_content resolution (decoders/gateway.py)


@dataclass
class TruthCar:
    """The hidden car. Close to an ID.3 58 kWh, but not the planner's figures."""
    mass_kg: float = 1880.0
    crr: float = 0.0095
    cda_m2: float = 0.66
    eff: float = 0.87               # battery -> wheels
    regen: float = 0.62             # share of braking/descent energy recovered
    aux_kw: float = 0.32
    heat_set_c: float = 19.5
    heat_kw_per_c: float = 0.16     # heating power per degree below the set point
    heat_max_kw: float = 5.0
    ac_kw_per_c: float = 0.07       # above 23 °C
    warmup_kwh_per_c: float = 0.04  # per degree below 16 °C, once per cold start
    cold_loss_per_c: float = 0.0055  # drivetrain loss per degree of battery below 18
    usable_kwh: float = 56.0


@dataclass
class SyntheticDrive:
    sample: DriveSample             # what the collector would have recorded
    true_kwh: float                 # what the battery really gave
    kind: str                       # "town" | "commute" | "motorway"
    outing: int                     # drives less than 2 h apart share one
    soc_start: float                # % of usable, measured
    soc_end: float
    true_temp_c: float
    headwind_ms: float
    rain: bool
    payload_kg: float
    trace: list[float] = field(default_factory=list, repr=False)   # km/h every 5 s


# Segment types: cruise km/h, stops per km, cruise variation.
_ROAD = {"town": (42.0, 1.6, 0.12), "rural": (82.0, 0.15, 0.08),
         "motorway": (116.0, 0.01, 0.05)}


def ambient_c(t_s: float, rng: random.Random, day_noise: dict) -> float:
    """A temperate climate: about 4 °C in January, 18 °C in July, colder at
    night, and a weather of the day on top."""
    day = int(t_s // DAY)
    doy = day % 365
    seasonal = 11.0 - 7.0 * math.cos(2 * math.pi * (doy - 20) / 365.0)
    hour = (t_s % DAY) / 3600.0
    diurnal = 3.0 * math.cos(2 * math.pi * (hour - 15.0) / 24.0)
    if day not in day_noise:
        day_noise[day] = rng.gauss(0.0, 3.0)
    return seasonal + diurnal + day_noise[day]


def _trace(rng, legs) -> tuple[list[float], list[tuple[int, float]]]:
    """A 5 s speed trace (km/h) through (road, km) legs, and the stops made on
    each: (count, cruise km/h)."""
    out: list[float] = []
    stops = []
    for road, km in legs:
        cruise, per_km, var = _ROAD[road]
        v_c = cruise * rng.uniform(1 - var, 1 + var)
        n_stops = max(0, int(round(km * per_km * rng.uniform(0.6, 1.4))))
        stops.append((n_stops, v_c))
        gap = km / (n_stops + 1)
        for _ in range(n_stops + 1):
            done = 0.0
            # accelerate (10 s), cruise, brake (10 s), wait (0-40 s)
            for k in range(2):
                v = v_c * (k + 1) / 3
                out.append(v)
                done += v / 3600.0 * SAMPLE_S
            while done < gap - v_c / 3600.0 * SAMPLE_S * 2:
                v = max(5.0, v_c * (1 + rng.gauss(0, var / 2)))
                out.append(v)
                done += v / 3600.0 * SAMPLE_S
            for k in range(2):
                out.append(v_c * (2 - k) / 3)
            out.extend([0.0] * int(rng.uniform(0, 8)))
    return out, stops


def _energy(car: TruthCar, trace, stops, temp_c, headwind_ms, rain, payload_kg,
            climb_m, batt_c, warm_start, style) -> tuple[float, float]:
    """(kWh from the battery, battery temperature at the end)."""
    m = car.mass_kg + payload_kg
    rho = 101325.0 / (287.05 * (temp_c + 273.15))
    crr = car.crr * (1 + 0.004 * max(0.0, 20.0 - temp_c)) * (1.08 if rain else 1.0)
    traction_j = 0.0
    km = 0.0
    batt = batt_c
    cold_j = 0.0
    for i, v_kmh in enumerate(trace):
        v = v_kmh / 3.6
        if v <= 0:
            continue
        va = v + headwind_ms * 0.7
        f = crr * m * G + 0.5 * rho * car.cda_m2 * abs(va) * va
        work = max(0.0, f * v * SAMPLE_S)
        traction_j += work
        cold_j += work * car.cold_loss_per_c * max(0.0, 18.0 - batt)
        km += v * SAMPLE_S / 1000.0
        if i % 12 == 0:              # each minute driving warms the pack a little
            batt = min(temp_c + 14.0, batt + 0.15)
    # Each stop brakes from, and accelerates back to, its own road's speed.
    stop_j = sum(n * 0.5 * m * (v_c / 3.6) ** 2 for n, v_c in stops) \
        * (1 / car.eff - car.regen)
    climb_j = m * G * climb_m
    hill_j = climb_j / car.eff if climb_j > 0 else climb_j * car.regen
    hours = len(trace) * SAMPLE_S / 3600.0
    if temp_c < car.heat_set_c - 2:
        hvac_kw = min(car.heat_max_kw, car.heat_kw_per_c * (car.heat_set_c - temp_c))
    elif temp_c > 23.0:
        hvac_kw = car.ac_kw_per_c * (temp_c - 23.0)
    else:
        hvac_kw = 0.0
    time_kwh = (car.aux_kw + (0.08 if rain else 0.0) + hvac_kw) * hours
    warm_kwh = 0.0 if warm_start else car.warmup_kwh_per_c * max(0.0, 16.0 - temp_c)
    kwh = ((traction_j + cold_j) / car.eff * style + stop_j + hill_j) / 3.6e6 \
        + time_kwh + warm_kwh
    return kwh, batt


def _plan_day(rng, day: int) -> list[tuple[float, str, list, float]]:
    """(start hour, kind, legs, net climb) for one day's drives."""
    weekday = day % 7 < 5
    out = []
    if weekday and rng.random() < 0.62:
        km = rng.uniform(14, 34)
        legs = [("town", km * 0.3), ("rural", km * 0.7)]
        climb = rng.gauss(0, 25)
        out.append((rng.uniform(7.3, 8.6), "commute", legs, climb))
        out.append((rng.uniform(16.8, 18.4), "commute", list(reversed(legs)), -climb))
    elif not weekday and rng.random() < 0.28:
        km = rng.uniform(60, 190)
        legs = [("town", 4.0), ("rural", km * 0.2), ("motorway", km * 0.8 - 4.0)]
        climb = rng.uniform(-150, 150)
        out.append((rng.uniform(8.5, 10.5), "motorway", legs, climb))
        out.append((rng.uniform(15.0, 18.0), "motorway", list(reversed(legs)), -climb))
    for _ in range(rng.choice((0, 0, 1, 1, 2))):
        km = rng.uniform(3, 14)
        # A town errand there and, 20-60 minutes later, back.
        h = rng.uniform(10.0, 20.5)
        climb = rng.gauss(0, 10)
        out.append((h, "town", [("town", km)], climb))
        out.append((h + km / 30 + rng.uniform(0.33, 1.0), "town", [("town", km)], -climb))
    return sorted(out, key=lambda d: d[0])


def journeys(days: int = 365, seed: int = 1, car: TruthCar | None = None,
             start_s: float = 1_746_057_600.0, glitches: float = 0.0
             ) -> list[SyntheticDrive]:
    """`days` of driving from `start_s` (default 2025-05-01 UTC: summer first,
    so a method meets its first winter having seen only warm drives).
    `glitches`: the share of drives whose energy reading is wrong, as a
    mis-paired reading or a charge in a recording gap would make it."""
    rng = random.Random(seed)
    bad = random.Random(seed + 7919)   # its own stream: the driving stays the same
    car = car or TruthCar()
    day_noise: dict = {}
    out: list[SyntheticDrive] = []
    odo = 12_345.3
    energy = car.usable_kwh * 0.9
    last_end = -1e18
    batt_c = 15.0
    outing = 0
    for day in range(days):
        wind = rng.weibullvariate(5.0, 2.0)       # m/s at 10 m, mean ~4.4
        wind_from = rng.uniform(0, 360)
        doy = day % 365
        rain = rng.random() < (0.35 + 0.12 * math.cos(2 * math.pi * (doy - 20) / 365.0))
        heading = rng.uniform(0, 360)
        for hour, kind, legs, climb in _plan_day(rng, day):
            t0 = start_s + day * DAY + hour * 3600.0
            if t0 < last_end + 300:
                t0 = last_end + 300
            temp = ambient_c(t0, rng, day_noise)
            parked_s = t0 - last_end
            if parked_s > 2 * 3600:
                outing += 1
            # The pack cools toward the air while parked (about 6 h to settle).
            k = 1 - math.exp(-parked_s / (6 * 3600.0))
            batt_c = batt_c + (temp - batt_c) * k
            trace, stops = _trace(rng, legs)
            heading = (heading + 180.0) if kind != "town" or rng.random() < 0.5 \
                else rng.uniform(0, 360)
            headwind = wind * math.cos(math.radians(wind_from - heading))
            payload = max(0.0, rng.gauss(90, 45))
            style = rng.gauss(1.0, 0.03)
            kwh, batt_c = _energy(car, trace, stops, temp, headwind, rain, payload,
                                  climb, batt_c, parked_s < 1800, style)
            dur = len(trace) * SAMPLE_S
            km_true = sum(v for v in trace) / 3600.0 * SAMPLE_S
            if energy - kwh < car.usable_kwh * 0.15:
                energy = car.usable_kwh * rng.uniform(0.7, 0.9)   # charged since
            e0 = energy
            energy -= kwh
            # As recorded: whole-km odometer, 25 Wh steps, BMS noise.
            def read(e):
                noisy = e + rng.gauss(0.0, 0.04)
                return round(noisy / ENERGY_STEP_KWH) * ENERGY_STEP_KWH
            km_read = math.floor(odo + km_true) - math.floor(odo)
            odo += km_true
            measured = read(e0) - read(energy)
            if glitches and bad.random() < glitches:
                measured += bad.choice((-1, 1)) * bad.uniform(1.5, 4.0)
            moving = [v for v in trace]
            temp_read = temp + rng.gauss(0, 0.5)
            v2 = sum(v ** 3 for v in moving) / max(1e-9, sum(moving))
            sample = DriveSample(
                distance_km=float(km_read), energy_kwh=measured, temp_c=temp_read,
                avg_speed_kmh=sum(moving) / len(moving), start_s=t0, end_s=t0 + dur,
                v2_kmh2=v2, net_climb_m=climb)
            out.append(SyntheticDrive(
                sample, kwh, kind, outing, e0 / car.usable_kwh * 100,
                energy / car.usable_kwh * 100, temp, headwind, rain, payload, trace))
            last_end = t0 + dur
    return out


def samples(drives: list[SyntheticDrive], climb_known: bool = False) -> list[DriveSample]:
    """The DriveSamples, as the collector sees them: no height unless asked
    (the real drives have none)."""
    out = []
    for d in drives:
        s = d.sample
        out.append(DriveSample(s.distance_km, s.energy_kwh, s.temp_c, s.avg_speed_kmh,
                               None, s.hvac_on, s.start_s, s.end_s, s.v2_kmh2,
                               s.net_climb_m if climb_known else None))
    return out
