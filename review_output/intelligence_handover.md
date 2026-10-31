# Navigation intelligence handover — 2026-10-31

Branch `navigation-intelligence`, from `navigation` at `eb646d3`. The tested
version is untouched:
- the APK on the Pixel is the build of `eb646d3`, with sha256 `a4b5d207…`;
- a copy is kept in `~/odb-scanner-tested/`;
- the local tag `tested-apk-20261031c` marks the commit.

No release build was made on this branch (only `testDebugUnitTest`), so
`~/odb-scanner-build/.../app-release.apk` is still that file. **Nothing
pushed or merged.** No car, adapter or real database was touched: every
full Python run was bracketed by the 100-entry snapshot of `~/odb_data` and
`data/`. The research is in `intelligence_review.md`, the steps in
`intelligence_progress.md`, and the tasks in `intelligence_backlog.md`.

## What changed

| | What | Commits |
|---|---|---|
| Energy | A physics-informed consumption model (drag by speed², heating and auxiliaries as power, cold, cold start; ID.3 physics prior; robust; no term below zero), judged chronologically against the flat rate and the linear model on the car's own drives -- never from a drive's own outing | `89d50c1`, `a8288b4`, `88d220b` |
| Ranges | Each leg's 80 % arrival range from the model's spread (journey part ∝ km, wind ∝ speed², the rest ∝ √km, model and forecast uncertainty), calibrated on drives predicted beforehand; confidence and reasons on the Route page | `a7f59a7` |
| Charging | "Find a charging stop": along the route, near the start, the destination or a place; judged for this car (arrival, minutes on its curve, average kW, cost, listing age); the best re-planned with the stop; "add this stop" / "instead of" through the existing picks. A chosen charger may be 30 km off the route (both planners, shared vectors) | `4a82084` |
| Curve | This car's DC curve, measured where sessions show its own limit (a slow charger is never taken for a slow car); `routing.charge_curve` now read | `af75c92` |
| Profile | Trips page "What this car has shown": measured, inferred (± uncertainty, "mostly assumed") and every recent drive against its prediction made beforehand | `d2df68b` |
| Phone | GPS fixes judged before they steer (late, vague, rough, jumping); restore keeps the driver's stops and says the position's age and what changed; evidence on every revision and suggestion, read by the assistant | `c6a8dc6`, `95afe0c` |
| Route page | The start position's age; the start charge checked against the displayed charge | `e1dfa7e` |

## Measured (synthetic only)

On a simulated year with a known truth (`tools/backtest_consumption.py
--synthetic`):

| | Physics | Linear | Flat |
|---|---|---|---|
| Error, kWh/100 km | **1.37** | 1.83 | 2.81 |
| Worst arrival miss, % of the battery | **10.4** | 15.5 | 22.8 |
| 80 % range held | **79 %** | 73 % | 69 % |

Across seven scenarios (other seeds, bad readings, 75 days only, a colder
climate, a heat-pump car, a heavier car), physics had the lowest error and
the smallest worst miss in all seven. On drives over 100 km it was best in
six and level in one. Its range held 77–85 % of drives. **This is
evidence about the method, not this car.**

## Verified

- **Python:** 1,666 passed, ruff clean, smoke test against the simulator
  26 passed, 0 failed.
- **Phone:** 180 JVM tests, 0 failures, including the 29 shared route cases,
  the hand-off and en-route vectors, and the new GPS, restore and evidence
  tests. Tested APK sha256 unchanged after every run.

## For you

1. **Run on the car's own drives** (read-only):

       .venv/bin/python tools/backtest_consumption.py --db ~/odb_data/odb_scanner.db --drives

   It says which method this car's drives support. With little driving
   recorded it will say there is not enough to judge, and the flat rate
   stays. Then look at the Trips page's "What this car has shown".
2. **After your phone test:** decide whether this branch's phone changes go
   into the next APK:
   - the GPS gate;
   - the restore that keeps your stops;
   - the evidence the assistant explains from;
   - chosen chargers off the route.

   None of them changes a screen or the Google Maps hand-off.
3. **Push / merge** `navigation-intelligence` when you are happy (it sits on
   `navigation`).
4. **Remove `analysis/journey_intelligence.py`?** Only its own test uses
   it; the profile supersedes it.

## Not done

- **Google Maps hand-off origin age** (IN15): a stale last-known fix would
  make Google Maps preview from the wrong place. Not changed during your
  test.
- **Phone port of the physics model, its ranges and the charger search
  screen** (IN14, IN06). The shared planner logic is ready.
- **Per-segment route speeds** (IN13): measured as not worth it yet.
- **A suggestion waiting at a restart is not kept** (IN16). The restore
  keeps the stops and the monitor suggests again.
