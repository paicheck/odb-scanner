# Overnight handover — 2026-10-31

Branch `overnight-2026-10-31`. **Nothing is pushed, merged, released or
delivered.** It sits on `charger-preferences`, which sits on `ai-fallback`,
which sits on `main` (`8b17356`); none of the three is on GitHub yet. The
step-by-step log, with every test count, is `overnight_progress.md`; the task
list is `overnight_backlog.md`.

Safety: no adapter, Bluetooth or vehicle was touched; the read-only allow-list,
token checks and safety tests are unchanged. Every full-suite run was
bracketed by a metadata snapshot of `~/odb_data` and `data/` (100 entries,
identical every time). No real database was opened. The release APK was built
(`~/odb-scanner-build/app/outputs/apk/release/app-release.apk`) but not copied
to a phone.

## What changed

| # | What | Commit |
|---|---|---|
| N01 | Health summary counted fault codes in a column that does not exist, so it always said 0. Now reads `dtcs` with their status, and judges the 12 V only from a fresh resting reading | `9bed741` |
| N02 | Planner counted a hill twice with a personal model, and swallowed model errors | `84af237` |
| N03 | Phone planner: charger preferences, picks and alternatives, as on the PC; phone settings for them | `a068f3a` |
| N04 | Consumption model: no more 5 coefficients from 3 drives, unknown conditions no longer mean 0 km/h, no extrapolation, weighted spread. A chronological backtest decides whether the Route page uses it. New: `tools/backtest_consumption.py` (read-only) | `fd90d89` |
| N05 | Forecast wind along the route (Open-Meteo, positions rounded to ~11 km); temperature only through the car's own model | `c951da2` |
| N06 | A likely arrival range for every stop and the destination, measured from the car's own prediction errors where possible | `2837dba` |
| N07 | Digital twin: each value observed / inferred / unknown, as of any moment, never reading later rows. `GET /api/twin?as_of=` | `29c4d39` |
| N08 | En-route assessment rebuilt on the phone's `Replan.kt` rules: the gap grows with distance; critical/adjust/warning/normal; never changes a route or claims a charger is free | `7d22acc` |
| N10 | AI reports: every `key@timestamp` citation is checked against the evidence actually sent; invented ones go into the stored caveats. The twin's state is now in the evidence | `9762ae5` |
| N09 | Overview: health card (on `collector.low_12v_voltage`) and "what the car is doing" | `67303d4` |
| — | The intermittent `test_trips` failure: found and fixed (see below) | `6b53b4f` |
| — | Decoder fuzz over all 409 decoders; "evidence is data, never an instruction" rule | `8b762b9`, `1706d24` |
| — | Phone: wind and arrival ranges via the shared vectors, the Open-Meteo fetch, a "Count the forecast wind" switch | `553cf61` |
| — | Twin lookup on the covering index; README, regression guards, CLAUDE.md | `dec3f42`, `30b671b` |

Verification at the end: **1,608 Python tests passed**, ruff clean;
**137 Android JVM tests, 0 failures** (bounded Gradle, `:app:testDebugUnitTest
:app:assembleRelease`), including all 27 shared route-vector cases;
`tools/smoke_test.py` against the simulator on a scratch database: 26 passed,
0 failed, including the read-only audit of every transmitted frame.

## Things found that you may want to know

- **The flaky trips test was a clock bug in the test.** Its data was stamped
  at module import while the code reads the clock at run time, with 30 minutes
  of slack. Shifting the old anchor 31 minutes back fails exactly that test.
  CLAUDE.md's "watch for it" note can go.
- **Speed and the awake 12 V are recorded under two keys each**
  (`speed_kmh`/`vehicle_speed`, `aux_12v_voltage`/`lv_voltage_obd`). The overview
  showed one, the trips/planner code read the other. The twin takes the newest.
- **`investigation_prompt` was never used**: the citation rules were not being
  sent to the AI. They are now, and checked.
- **405 of 409 decoders raise on a too-short response.** That is contained by
  `DIDSpec.decode_value` (value None, raw bytes kept, a WARNING logged), and is
  now pinned by a test. A truncated reply from the car will log a warning per
  key rather than crash.

## Decisions for you

1. **Push / merge.** Suggested order: `ai-fallback`, then `charger-preferences`,
   then this branch (each builds on the one before). Or review the 33
   commits over `main` as one PR.
2. **Weather is on by default** (`routing.weather: true`, and on the phone).
   It sends points along the route, rounded to 0.1°, to Open-Meteo; the route
   already goes to OpenRouteService and Open Charge Map. Turn it off in
   `config.local.yaml` / Route settings if you would rather not.
3. **Deliver the phone update?** Build is ready; before copying, check
   `apksigner verify --print-certs` shows `d6967d0e…37feee` (CLAUDE.md). The
   HONOR X8a still runs 20261029c.
4. **A prediction ledger** (store each planned trip's arrival and compare it
   with the real one later) needs a new table. Not done: schema changes are
   yours to approve.

## Worth running yourself

- `.venv/bin/python tools/backtest_consumption.py --db ~/odb_data/odb_scanner.db --drives`
  — read-only (`mode=ro`); says whether the car's own model beats the flat
  rate on its real drives, and by how much. With little driving yet recorded
  it will say there is not enough to judge, which is the right answer.
- The overview and `/api/twin` on the real database: the twin's "what the car
  is doing" and the health card have only seen synthetic data.

## Not done, and where to start

- **Phone en-route levels.** `analysis/enroute.py` follows `Replan.kt`'s rules
  but the phone does not show a level yet. Next step: port `assess()` to
  Kotlin and hold both to a shared vector file, as the route vectors do.
- **Phone personal model.** The phone plans with the flat figure; the model
  and its backtest are PC-only.
- **Wind forecast error** is not in the arrival range; a long leg carries one
  drive's spread, so ranges are on the wide side.
- **Citation values**: a cited key/moment is checked, but a number quoted in
  the text is only shown beside the evidence value, not compared.
