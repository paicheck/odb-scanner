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
