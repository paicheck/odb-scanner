# Navigation intelligence progress log — 2026-10-31

## Start

- `main`, `navigation`, `origin/main`, `origin/navigation`: all `eb646d3`;
  `maps-voice` (`8167a25`) is inside `navigation`. Working tree clean.
- Tested APK: `~/odb-scanner-build/app/outputs/apk/release/app-release.apk`,
  sha256 `a4b5d207…80b2cd`, the same file as the Pixel's
  `Download/odb-scanner-0.2.0-release-20261031c.apk`. Copied to
  `~/odb-scanner-tested/`; local tag `tested-apk-20261031c` on `eb646d3`.
- The Gradle build directory is shared by every branch
  (`android/app/build.gradle.kts` sets `~/odb-scanner-build/app`), so this
  branch runs only `testDebugUnitTest`, never `assembleRelease`.
- Branch `navigation-intelligence` created from `eb646d3`.
- CI on `main` (`eb646d3`): success on every job.

## IN01–IN05: energy prediction

- `89d50c1` synthetic journeys (IN01). `a8288b4` physics model + three-way
  chronological backtest + tool (IN02, IN03). `a7f59a7` physics arrival
  ranges with confidence (IN04, IN05).
- Measured and fixed along the way:
  - The first fit's worst misses were motorway drives with a tailwind, so
    the wind part of the spread now grows with speed².
  - A loose base prior took town stop-start and charged it to the
    motorway; the base is now held near rolling resistance.
  - The truth simulator priced stops at the drive's mean speed; fixed in
    the generator.
  - Mean-based calibration was swayed by bad readings (96 % held); it is
    now median-based (79 %).
  - Glitches shared the driving's random stream; they now have their own.
- Commit 2 tested alone in a temporary worktree (166 tests, ruff clean)
  before commit 3.
- Full suite after IN05: **1,643 passed**, ruff clean; the 100-entry
  snapshot of `~/odb_data` and `data/` identical before and after.
- Route speeds measured, not changed (review §5).

## IN06–IN07: charging

- `4a82084` charger search. Tests found and fixed:
  - a planned stop read its own departure charge (a 0.0000001 km rounding);
  - a stop "after" itself gave −32 %;
  - chargers just past a stop showed the charge after it;
  - "instead of" was only set for re-planned chargers.

  My own test assumptions were also wrong: every charger can be added; a
  one-charger trip that cannot finish; a 50 kW gap of 10 min where it is 7.
- Both planners: a chosen charger up to 30 km off the route; 2 new route
  vectors. Kotlin 170 JVM tests, 0 failures; tested APK unchanged
  (`a4b5d207`).
- `af75c92` measured DC curve, and `routing.charge_curve` read at last.

## IN08: planned against actual, profile

- `d2df68b` profile on the Trips page and `/api/vehicle-profile`. Reading
  it on synthetic data showed a −0.32 kWh cold start, so `88d220b` holds
  any coefficient at zero rather than below. Scenarios: same or better.
- Full suite: **1,664 passed**, snapshot identical.

## IN09–IN10: resilience, evidence

- `c6a8dc6` GpsGate (stale, vague, rough, jump), restore note, markSent
  guard. Kotlin 178, 0 failures.
- `95afe0c` Evidence on revisions and suggestions (saved, read by the
  assistant); restore keeps the driver's stops still on the way. Kotlin
  180, 0 failures. Tested APK unchanged.
- `e1dfa7e` Route page: the start position's age, and the start charge
  checked against the displayed charge.

## IN11–IN12

- Architecture review, review §10. Docs: README, REGRESSION_GUARDS,
  CLAUDE.md, handover.
- Final: **1,666 Python tests passed**, ruff clean, smoke test 26 passed / 0
  failed / 1 skipped (simulator, scratch database), snapshot identical; Kotlin
  180 JVM tests, 0 failures; tested APK sha256 `a4b5d207…` unchanged.
