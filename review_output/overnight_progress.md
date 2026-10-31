# Overnight progress — 2026-10-31

One entry per completed task, newest last, timed by its commit. Tests are run with
`.venv/bin/python -m pytest -q -p no:cacheprovider` (random order, as CI) unless
noted; "real data unchanged" means a metadata snapshot of `~/odb_data` and
`data/` was identical before and after the run.

## 01:06 — Baseline

- Branch `overnight-2026-10-31` from `8fc50cc`.
- Full suite: **1,524 passed** (176 s). Real data unchanged.
- ruff: **2 errors** (unused imports, `tests/test_health_summary.py`).
- Audit of the previous session's modules (findings drive N01–N08):
  - `analysis/health_summary.py`: queries `diagnostic_events.resolved`, a
    column that does not exist; the `except` returns 0, so the fault count is
    always 0 and the status never reflects stored fault codes. Fault codes
    live in `dtcs`. 12 V from `control_module_voltage` with no freshness,
    provenance or awake/asleep distinction. Its test asserts only that keys
    exist. Overview passes `health` to a template that never renders it.
  - `analysis/consumption_model.py`: up to 5 coefficients from as few as 3
    drives (an exactly-fitted model reports ±0.01 kWh/km); distance-weighted
    fit but unweighted residual spread; linear extrapolation outside the
    training range; a feature is dropped for all drives if one lacks it; no
    backtest.
  - `routing/planner.py`: `segment_kwh` derives a per-stretch climb rate and
    feeds it to the model's elevation coefficient while also adding the
    climb's potential energy (latent double count); `_model_rate` swallows
    every exception.
  - `routing/car.py`: `car_energy` builds the model on every Route page load
    (two more 90-day queries); the Route page never passes it to the planner.
  - `analysis/enroute.py`: carries the current SoC gap forward unchanged,
    though a higher consumption rate makes the gap grow with distance; the
    phone already has `TripMonitor.kt`/`Replan.kt`.
  - `routing/weather.py`: current conditions only (no forecast for the trip
    time), not used anywhere yet; headwind sign checked correct.

## 01:07 — N01 Health summary: real fault codes, 12 V judged at rest — `9bed741`

- Files: `analysis/health_summary.py`, `tests/test_health_summary.py`.
- Fault codes from `dtcs` with their stored status (warning/active → attention;
  pending/confirmed → attention if seen in 7 days; stored → history). 12 V judged
  only from a resting `adapter_12v_voltage` ≤ 2 days old; awake `aux_12v_voltage`
  shown, never judged; simulated/imported ignored; every reading carries ts and
  age; "incomplete" when nothing is wrong but something could not be checked.
- Tests: 8 new (replacing a key-only test); all 8 fail against the old module.
- Limitation: the overview still does not render it (N09).

## 01:08 — N02 Planner: a hill counted once; no silent model fallback — `84af237`

- Files: `routing/planner.py`, `tests/test_route_planner.py`.
- `segment_kwh` no longer derives a per-stretch gradient for the model's rate
  (hills are physics only); `plan_trip`/`segment_kwh` no longer accept a
  gradient; `_model_rate` no longer swallows exceptions.
- Tests: 2 new; the hill test fails when the double count is reintroduced.
  Route/planner/preference/model tests: 89 passed. ruff clean.

## 01:18 — N03 Phone planner: charger preferences, as on the PC — `a068f3a`

- Files: `android/.../route/Planner.kt`, `RouteServices.kt`, `Trip.kt`,
  `RouteScreen.kt`, `PlannerTest.kt`.
- Kotlin planner follows prefer/avoid networks, public-only, avoid/use
  chargers, and lists up to 4 alternatives per stop, in the same order as
  Python. OCM `ID` and `UsageTypeID` kept. Route settings gain "Prefer",
  "Never use", "Public chargers only"; stops show access, preferred, and the
  chargers also near them. (Per-stop avoid/use picks are PC-only for now.)
- Tests: PlannerTest now reads the new settings from the shared vectors and
  compares each stop's charger ID, preferred flag and alternatives with
  Python's (22 cases; 18 stops, 11 with alternatives, 2 preferred). Bounded
  Gradle (`choom`, `-Xmx1536m`, 2 workers, `timeout 1500`):
  `:app:testDebugUnitTest :app:assembleRelease` **130 tests, 0 failures**,
  release APK built (not delivered).
- Mishap, repaired: calling `tools/export_route_vectors.py --check` (it has no
  such flag) ran the exporter mid-edit and truncated
  `tests/data/route_vectors.json`; restored from git and regenerated after the
  edit: byte-identical.

## 01:30 — N04 Consumption model fixed, backtested, used only when earned — `fd90d89`

- Files: `analysis/consumption_model.py` (rewritten),
  `analysis/consumption_backtest.py` (new), `routing/car.py`,
  `routing/planner.py`, `web/routes/route.py`, `web/templates/route.html`,
  `tools/backtest_consumption.py` (new), README tools table; 4 test files.
- Model: 5 drives per coefficient; minimum spread per feature; a feature is
  kept only if it cuts the leave-one-out error by 5 % on the same drives
  (2 % let a spurious speed term in on synthetic data); centred features, so
  an unknown condition means the usual one (the old base was the rate at
  0 km/h / 0 °C); inputs clamped to the range seen; distance-weighted spread;
  a feature some drives lack is tried on those that have it.
- Backtest: each of the newest 60 drives predicted only from drives that
  ended before it began, inside the planner's 90-day window; flat (the
  planner's measured rate) vs model; errors in kWh/drive and kWh/100 km,
  bias, band coverage. Verdict "model" only with ≥ 10 real model predictions
  and ≥ 5 % lower error.
- Route page: uses the model (with the route's average speed) only when the
  backtest earned it and the driver did not change the consumption figure;
  says why either way; assumptions name the conditions used and any clamped.
  Cached per vehicle until a new odometer reading or the next day: 50 ms
  uncached on 100 synthetic drives, 2 ms cached.
- Synthetic evidence (seeded): a car that feels the cold — flat 2.1 vs model
  1.0 kWh/100 km off, flat bias +1.9 (it lags the season); a car with no
  pattern — model chooses no feature, identical to flat.
- Tests: 8 new model tests (all 8 fail against the old module), 6 backtest,
  5 Route-page, 2 tool. Full suite **1,554 passed** (176 s), ruff clean, real
  data unchanged (100-entry snapshot identical).
- The tool opens the database `mode=ro`; on a WAL database SQLite may create
  its empty `-wal`/`-shm` coordination files — the test checks the database
  bytes are unchanged and no transaction was written. Not run on the real
  database (owner-run: `tools/backtest_consumption.py --db ~/odb_data/odb_scanner.db`).
- Limitation: the phone has no personal model yet (it plans with the flat
  figure); no temperature is passed yet (N05).

## 01:43 — N05 Weather in the route energy — `c951da2`

- Files: `routing/weather.py` (rewritten), `routing/planner.py`,
  `web/routes/route.py`, `web/templates/route.html`, `config.yaml`
  (`routing.weather: true`), `tests/conftest.py`, `tests/test_weather.py`.
- Forecast: one Open-Meteo GET for points every 25 km (≤ 24), hourly, the hour
  nearest each point's ETA (leaving now at the routing provider's time; charging
  time not added). Positions rounded to 0.1° (~11 km) before sending; cached
  30 min per rounded position. Failure → no forecast, said on the page.
- Planner (pure; forecast passed in as `Conditions` by km): per-stretch
  headwind from the stretch's bearing; drag energy against calm air (CdA 0.63
  m², wind at the car 70 % of the 10 m figure, air density from temperature).
  30 km/h headwind at 100 km/h ≈ +4.3 kWh/100 km; the same tailwind −3.5.
  Temperature goes only to the car's own model and replaces a single
  temperature rather than adding to it; with the flat figure it is not counted,
  and the assumptions say so.
- Tests: 12 new in `test_weather.py` (physics, per-stretch lookup, temperature
  only through the model, sampling, rounding, cache, failure, programming error
  not swallowed, Route page on/off/offline). Mutation: sending unrounded
  positions fails the rounding test.
- New suite-wide guard: any real HTTP request through `requests` outside
  loopback fails as offline (one loopback ingest test needed the exception).
- Full suite **1,566 passed**, ruff clean, real data unchanged.
- Decision for the owner: weather is on by default (the route was already sent
  to OpenRouteService and Open Charge Map); `routing.weather: false` turns it
  off. Phone: no weather yet.

## 01:49 — N06 Arrival ranges — `2837dba`

- Files: `analysis/consumption_backtest.py` (`error_spread`),
  `routing/planner.py` (`RateError`, `_add_bands`), `routing/car.py`,
  `web/routes/route.py`, `web/templates/route.html`, `web/static/dashboard.css`;
  `tests/test_arrival_range.py` (7).
- Every stop and the destination get a likely range: the planned figure moved
  by the leg's distance × how much more/less than planned the car may use.
  Measured where possible: the distance-weighted 10th–90th percentile of how
  the car's recent drives differed from their backtest predictions, for the
  figure actually in use (model or flat). Otherwise (too few drives, a typed or
  configured figure) an assumed ±10 %, labelled as an assumption.
- A range reaching below the reserve is marked; plans are still chosen for the
  planned figure (the range shows the room left, it does not re-plan).
- Limitation: a whole leg carries one drive's spread, so long legs are on the
  wide side; forecast error in the wind is not in the range; the phone shows
  no range yet.
- Full suite **1,573 passed**, ruff clean, real data unchanged.

## 01:57 — N07 Digital twin foundation — `29c4d39`

- Files: `analysis/twin.py` (new), `analysis/freshness.py` (public
  `classify`), `web/routes/twin.py` (new, `GET /api/twin?as_of=`),
  `web/dashboard.py`; `tests/test_twin.py` (11).
- Each quantity is observed (newest reading ≤ as_of, with ts, age, freshness
  verdict, provenance, decoder status; "current" per quantity: SoC 15 min,
  speed 2 min, usable capacity 30 days, resting 12 V 2 days), inferred (charge
  from energy ÷ usable; mode driving/charging/parked/asleep with evidence;
  standing since — stamped with the oldest input) or unknown (never a zero).
- As of: rows after the moment are never read; mutation (dropping the bound)
  fails 2 tests. Simulated rows ignored; imported never current.
- Prediction vs observation: the Route page's consumption figure on the latest
  drives, each predicted from the earlier ones (now only, not as-of).
- No schema change: a persistent prediction ledger (e.g. a planned trip's
  arrival vs the real one) would need a table; left for the owner to decide.
- Full suite **1,584 passed**, ruff clean, real data unchanged.

## 02:03 — N08 En-route assessment on the phone's rules — `7d22acc`

- Files: `analysis/enroute.py` (rewritten), `tests/test_enroute.py` (13).
- Same thresholds as `Replan.kt` (drift 3 %, above 5 %, min used 3 %, at-stop
  3 km, reading max age 15 min, ratio clamp 0.75–1.5). Projects the charge at
  the next stop/destination at this drive's real/planned ratio, so the gap
  grows with distance (the old code carried it forward unchanged).
- Levels critical/adjust/warning/normal; critical and adjust need a projected
  shortfall ≥ 3 % against the plan (a real plan aims near the reserve — found
  when a real planner output graded "on plan" as "adjust"). Stale readings and
  planned stops are not judged. Critical lists the chargers the plan knows
  within the remaining reach or says to re-plan to search; availability is
  never claimed; nothing changes a route.
- Full suite **1,595 passed**, ruff clean, real data unchanged.
- Not done: a Kotlin port with shared vectors so the phone shows the same
  levels (the phone already re-plans and announces on drift; it has no level).

## 02:09 — N10 AI citations checked against the evidence — `9762ae5`

- Files: `ai/citations.py` (new), `ai/prompts.py`, `ai/reports.py`,
  `ai/service.py`, `web/routes/ai.py`, `web/templates/ai.html`;
  `tests/test_ai_citations.py` (5).
- Found: `investigation_prompt`/`CITATION_RULES` existed but were never sent;
  nothing checked a citation.
- Now every question carries the rules; each `key@timestamp` in the answer is
  looked up in the packet actually sent (by key/code/source or the name it is
  filed under; moments compared to the second, UTC). Verified ones are listed
  with the evidence's own value; unsupported ones (unknown key / no reading at
  that moment) go into the stored AUTOMATIC CAVEATS; "cites no evidence" when
  there are none.
- The evidence now includes the twin's vehicle state (key, moment, observed /
  inferred / unknown). No VIN in it; cloud redaction unchanged
  (`test_a_cloud_report_carries_no_vin` still passes).
- Full suite **1,600 passed**, ruff clean, real data unchanged.
- Limitation: a value quoted in the text is not compared with the evidence
  value (only shown beside it); small local models may not cite at all — the
  caveat then says so on every report.

## 02:15 — N09 Overview: health and twin cards — `67303d4`

- Files: `web/routes/overview.py`, `web/templates/overview.html`,
  `analysis/twin.py`; `tests/test_overview_health.py` (4), `tests/test_twin.py` (+1).
- Health card (status, reasons, codes needing attention, resting 12 V with age
  judged on `collector.low_12v_voltage`, capacity trend) and a twin card (mode
  with evidence, standing since, observed/current/inferred/unknown counts, link
  to `/api/twin`).
- Found: speed is recorded under two keys (`speed_kmh` from the MEB DID,
  `vehicle_speed` from PID 0x0D) and the awake 12 V under two
  (`aux_12v_voltage`, `lv_voltage_obd`); the twin now takes whichever is newest.
- Full suite **1,605 passed**; `tools/smoke_test.py` against the simulator on a
  scratch database: **26 passed, 0 failed** (incl. the read-only TX audit);
  ruff clean; real data unchanged.

## 02:20 — The intermittent trips test, explained — see commit log

- `tests/test_trip_log_totals_and_what_the_cold_costs` (CLAUDE.md: "failed once
  and could not be reproduced"): the fixture stamped data relative to a NOW
  taken at module import; `trip_log` reads the real clock at test time; the
  one-day window has 30 min to spare. A stall > 30 min between collection and
  run fails it. Reproduced by shifting the old anchor 31 min back: exactly that
  test fails, no other. Fixed by anchoring in the fixture; assertions unchanged.
  25 random orders before the fix all passed (order was not the cause).

## 02:30 — Hardening: decoder fuzz; evidence is data — two commits

- `tests/test_decoder_fuzz.py`: seeded fuzz over all 409 decoders (meb 289,
  eup 120) with empty/short/random responses. Measured: 405 of them raise
  `IndexError` on a too-short response (by design; `DIDSpec.decode_value`
  contains it, logs the raw bytes, returns None); none returns NaN/∞. The test
  pins the containment (narrowing the `except` fails it).
- `ai/prompts.py` CITATION_RULES: text inside the evidence is never an
  instruction. Test: a stored fault description that tries to override the
  rules reaches the prompt before the rule that disarms it.
- Full suite **1,608 passed**, ruff clean, real data unchanged.

## 02:30–02:45 — Phone: wind and arrival ranges; docs; twin query — `553cf61`, `30b671b`, `dec3f42`

- Shared route vectors: 5 new cases (headwind then calm, cold tailwind,
  crosswind on a hill, measured lopsided range, range below the reserve); every
  case now records each stop's range, the arrival range and the wind energy.
  Python regenerated; Kotlin `Planner.kt` ported (Conditions, windKwh, bearing,
  RateError, bands). `PlannerTest` compares all of it.
- `route/Weather.kt`: the Open-Meteo fetch as `routing/weather.py` (25 km
  samples ≤ 24, one GET, ETA hour, 0.1° rounding, 30 min cache, failure notes);
  "Count the forecast wind" switch; ranges on the plan headline, each stop and
  the assistant's trip summary. `WeatherTest` (7) with an injected GET.
- Two bounded Gradle runs (`choom`, `-Xmx1536m`, 2 workers, `timeout 1500`):
  130 then **137 JVM tests, 0 failures**; release APK built 02:29, not delivered.
- `analysis/twin.py` standing-since lookup now reads only covered columns
  (EXPLAIN: covering-index search) — the overview builds the twin every load.
- README, `docs/REGRESSION_GUARDS.md`, CLAUDE.md open items updated.
- Full suite **1,608 passed**, ruff clean, real data unchanged.
