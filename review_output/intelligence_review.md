# Navigation intelligence review — 2026-10-31

What the energy prediction, charging choice and navigation recovery do now,
what is wrong with them, and what this branch changes. Results are filled in
as each step is measured (`intelligence_progress.md` has the log).

**No real journey data was read.** CLAUDE.md forbids opening the live
database. Every number below comes from synthetic journeys with a known
truth (IN01). Those numbers show whether a *method* works; they say nothing
about this car. The read-only command for the owner's own data is in the
handover.

## 1. What the car records, as far as energy goes

From `spec/odb_scanner.json` (profile `meb`) and `config.yaml`:

| Reading | Key | Poll | Status | Use |
|---|---|---|---|---|
| Distance | `odometer_km` | slow (60 s) | provisional | whole kilometres, so a drive's length is ±1 km |
| Battery energy | `hv_energy_content` | slow (60 s) | **verified** | 25 Wh steps |
| Usable capacity | `hv_energy_max` | slow | **verified** | |
| Speed | `speed_kmh` (UDS), `vehicle_speed` (OBD PID 0x0D) | fast (5 s) | unverified | the same quantity under two keys; drive samples read only the first |
| Outside temperature | `outside_temp` | slow | unverified | |
| Heater / A/C | `ptc_current`, `a_c_compressor_power_consumption_actual_value`, `hv_aux_power` | fast | unverified | not used: an unverified decoder is not a basis for a model |
| Position | phone uploads / `gps_position` | — | — | no elevation for drives (none is fetched: sending driven tracks to an elevation service would send private journeys out) |
| Charging | `charging_samples` (power, SoC, battery temperature per sample) | slow | — | a measured charging curve is possible (IN07) |

## 2. The consumption model as it was

`analysis/consumption_model.py`: a weighted linear regression of each drive's
kWh/km on outside temperature, average speed, heating and climb. Features are
selected by leave-one-out gain and are only allowed with enough drives and
spread. Predictions are clamped to the conditions seen.
`analysis/consumption_backtest.py` predicts each drive from the earlier ones
only (90-day window). The Route page uses the model only if it beats the flat
rate by 5 % over at least 10 predictions. That guard is sound and is kept.

Problems found by reading the code (the first two are checked on synthetic
data in §4):

1. **Speed enters as a straight line, and as the route's average.** Air drag
   grows with the *square* of speed. A linear speed term fitted on town and
   country drives undershoots the motorway. The planner passes
   `distance / duration` for the whole route. On a route that is half town
   and half motorway, that average (48 km/h for 30 and 120 km/h halves) puts
   the squared speed at a third of its real value.
2. **Heating is power, not energy per km.** A heater draws kW for as long as
   the car is driven. Per km that is more at 30 km/h than at 110 km/h, and
   more again on a short cold drive that has to warm the cabin from cold. A
   straight line in temperature cannot represent either.
3. **The 90-day window forgets winter.** A flat rate needs it, because it
   cannot tell the seasons apart. A model that knows temperature does not:
   last winter's drives are what predicts this winter.
4. **Clamping hides extrapolation instead of reporting it.** A trip colder
   or faster than anything seen is planned at the edge of the data, with the
   same confidence as a typical one.
5. **No defence against a bad drive.** A drive with a mis-paired energy
   reading or an unseen charge weighs as much as any other.
6. **Arrival ranges** (`planner._add_bands`): the 10th–90th percentile of
   recent drives' per-km errors, times the leg length. That treats a whole
   leg's error as one drive's, so a 200 km leg gets 20 times a 10 km drive's
   per-km error. The odometer's ±1 km on a 10 km drive is then counted as if
   it repeated over every 10 km of the leg. There is no model uncertainty, no
   term for an unknown condition, and no forecast error. The handover already
   named this.
7. **Speed samples** are read from `speed_kmh` only; drives logged through
   the OBD PID have no speed at all.

## 3. Design (IN03, IN04)

### The model: physics terms, a physics prior, a robust fit

`analysis/energy_model.py`. kWh/km is the sum of terms each linear in its
coefficient, so the fit stays a weighted least squares:

| Term | Feature | Prior (ID.3 58) | What it stands for |
|---|---|---|---|
| base | 1 | 0.052 ± 0.012 kWh/km | rolling resistance through the drivetrain (Crr 0.0075–0.0095, 1.8–2.0 t, 85–92 %) |
| drag | mean(v²)/(100 km/h)² × ρ/ρ₁₅ | 0.094 ± 0.030 | ½ρ·CdA·v²/η, CdA 0.63 m² |
| time | hours per km | 0.6 ± 0.6 kW | auxiliaries plus town stop-start |
| heat | max(0, 18 − T) × hours/km | 0.15 ± 0.12 kW/°C | a heater is power, so slower drives pay more per km |
| cold | max(0, 15 − T)/10 | 0.010 ± 0.020 | cold pack, cold tyres |
| warmup | max(0, 15 − T)/10/km | 0.40 ± 0.40 kWh | warming the cabin from cold, once per drive |

Why a prior. The car will have tens of drives, mostly in town. With no
prior, five coefficients from town drives say nothing reliable about the
motorway. Each coefficient therefore starts from physics and moves as far as
the drives justify. The prior is not tuned to the synthetic truth: the truth
car's own figures differ (CdA 0.66, η 0.87, heater 0.16 kW/°C, a
battery-temperature cold loss that the features only approximate). Only the
base is held tightly, because rolling resistance is known to ±25 %. A loose
base absorbed town stop-start and then charged it to the motorway (measured:
+2.2 kWh/100 km on cold motorway drives).

Robustness. A drive whose rate no ID.3 can produce (outside 4–60 kWh/100 km)
is left out and counted. The rest are fitted by iteratively reweighted least
squares with a Huber weight at 2.5 standardised residuals, so a mis-paired
energy reading or a charge hidden in a gap barely moves the fit (test:
< 3 %).

Variance, in kWh/km², estimated from the drives and shrunk toward a prior:
- **journey:** persists over a leg, so its sd grows with km;
- **wind:** also persists over a leg, and grows with speed². The prior is
  from drag physics with wind along the road at 3.5 m/s sd;
- **local:** averages out, so its sd grows with √km;
- **measurement:** the odometer's whole km (±0.41 km) and the BMS energy
  estimate (0.04 kWh per reading).

The coefficients' posterior covariance is carried into every prediction. An
unknown condition is set to the training mean, and its spread adds to the
variance.

What it does not do. It does not include speed profiles within a route
(see §5); hills stay with the planner's mgh, so the model learns the flat
rate (a drive's climb, when known, is taken off its energy first); and it
uses no unverified heater or A/C decoder.

### Choosing a model: the backtest decides, not the complexity

`analysis/consumption_backtest.py evaluate`. Each drive is predicted only
from drives that ended before it began and were not in its own outing
(drives less than 2 h apart), so there is no leakage from the same journey.
- **Training windows:** 90 days for the flat rate and the linear model, as
  the planner uses them. The physics model gets 365 days, because it knows
  temperature: last winter predicts this winter instead of misleading it.
- **Recommendation:** a model is used only if it beats the flat rate by 5 %
  over at least 10 drives it really predicted. Otherwise the flat rate
  stays, as before.

### Ranges: per leg, by length, calibrated

`routing/uncertainty.py ModelRange` (PC only). A leg's 80 % range is:

    plan ± t(0.9, n) × k × sd(leg)

- **sd(leg)** comes from the model:
  - km² × (coefficient variance + journey + wind × u × share);
  - plus km × local;
  - plus the end readings;
  - plus the forecast temperature's error (1.5 °C) through the temperature
    terms.
- **k** is how far off the model really was, learned on the backtest's drives
  as median(z²)/0.455, so a few corrupt drives cannot widen everything. It
  is shrunk toward 1 with 10 pseudo-drives and never below 0.8.
- **t** is Student's t for the drives k rests on: with little evidence, the
  range widens.
- **share** is 0.33 when the forecast wind is counted, otherwise 1.

Legs are independent, because each stop charges to a planned level. A trip
total would not be: the journey part is shared across legs.

Each range carries its confidence and reasons:
- **low:** fewer than 10 drives, or colder than anything seen, or legs over
  twice the longest drive;
- **good:** 40+ drives, 30+ calibration drives, and nothing unusual;
- **checks:** also flagged are faster than anything driven, a battery
  reading over 6 h old, and drives left out.

## 4. Validation results (IN02)

Synthetic only (IN01): `tools/backtest_consumption.py --synthetic`. Every
drive after the first is predicted from earlier ones.

**Year, seed 1** (940 drives, 818 predicted):

| Method | Error kWh/100 km | Bias | Arrival error mean / worst (% of battery) | 80 % range held | ≥100 km drives error | ≥100 km range held |
|---|---|---|---|---|---|---|
| flat (90 d) | 2.81 | −0.46 | 1.20 / 22.8 | 69 % | 2.54 | 90 % |
| linear (90 d) | 1.83 | −0.16 | 0.78 / 15.5 | 73 % | 1.49 | 85 % |
| physics (365 d) | **1.37** | +0.15 | **0.58 / 10.5** | **79 %** | **1.35** | 88 % |

By temperature (flat / linear / physics): below 5 °C 3.41 / 2.13 / 1.64;
5–15 °C 2.64 / 1.83 / 1.39; 15 °C and above 2.82 / 1.73 / 1.25. By speed:
under 40 km/h 3.56 / 2.51 / 1.56; 40–70 km/h 2.54 / 1.68 / 1.24; 70 km/h and
above 2.71 / 1.66 / 1.41. The flat rate's 80 % range held 60 % of 5–10 km
drives and 90 % of drives over 100 km. That is the "one drive's error times
the leg" problem in both directions.

**Robustness**, physics only: error, error on ≥100 km drives, range held,
worst arrival miss.

| Scenario | Error | ≥100 km | Range held | Worst miss | Next best overall |
|---|---|---|---|---|---|
| seed 2 | 1.36 | 1.34 | 78 % | 12.2 % | linear 1.92 |
| seed 3 | 1.63 | 2.16 | 78 % | 13.8 % | linear 2.15 (≥100 km: 2.14, a tie) |
| 3 % bad readings | 1.94 | 1.57 | 79 % | 12.8 % | linear 2.52 |
| first 75 days only | 1.71 | 2.14 | 81 % | 9.3 % | linear 2.35 |
| 6 °C colder climate | 1.81 | 2.08 | 77 % | 17.0 % | linear 2.61 |
| heat-pump car | 1.42 | 1.67 | 78 % | 11.7 % | linear 1.80 |
| heavier, draggier car | 1.81 | 2.41 | 78 % | 17.2 % | linear 2.33 |

What the measurements changed along the way:
- **Speed-dependent spread.** The first fit's worst misses were all motorway
  drives with a strong tailwind, which reverses on the way back. That led to
  the speed² wind term in the spread.
- **Truth simulator fixed.** The base-rate bias showed up a fault in my own
  truth simulator: stops were priced at the drive's overall average speed.
  It was fixed in the generator, not by tuning the model.
- **Robust calibration.** With 3 % bad readings, the mean-based calibration
  widened every range until it held 96 % of drives. The median-based one
  holds 79 %.

**What this shows and what it does not.**
- **Shown:** with drives measured as coarsely as this collector measures
  them, a physics-shaped model with a physics prior predicts better than a
  flat rate or straight lines. Its ranges are about as wide as they claim,
  including when the car, the climate or the amount of data changes.
- **Not shown:** how this car behaves. A real car will have its own errors
  and may have fewer motorway drives. On its own drives the backtest decides
  and can still keep the flat rate.

## 5. Route speeds: measured, not changed

The planner gives the model the route's average speed, and the model takes
mean(v²) as 1.2 × that squared (ROUTE_V2_RATIO). On the 22 synthetic motorway
drives after the first 600 (kWh/100 km off):

| Speed given | Bias | Error |
|---|---|---|
| the exact mean v² from the speed trace | +0.88 | 1.50 |
| route average including stops | −0.65 | 1.70 |
| free-flow average (as a routing service gives) | +0.44 | 1.57 |

The differences are within the noise of 22 drives, so per-segment speeds from
the routing service are not worth a provider change on this evidence. They
would matter for a route that is half town and half motorway, which is rare
for a trip that needs planning. The change is left in the backlog with this
measurement.
