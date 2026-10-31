# AI and voice progress log — 2026-10-31

## Start

- Branch `ai-voice` from `navigation-intelligence` (`e66ccb4`), the latest
  navigation work. `navigation` stays at `eb646d3`; the tested APK
  (`a4b5d207…`, copy in `~/odb-scanner-tested/`) is not rebuilt: this branch
  runs `testDebugUnitTest`, `lintDebug` and `assembleDebug` only (the debug
  APK goes to its own folder).
- Findings report written before any change: `ai_voice_review.md` §1–3.

## Implementation

- `0272003` (AV01, AV02):
  - First full run: 3 older tests failed on wording. A 500 ms timeout
    rounded to "0 s", and the fallback note printed a category for
    unstructured errors. Both fixed, keeping the old wording.
  - Commit tested alone in a worktree: build and unit tests pass.
- `3040f06` (AV03–AV05): tested alone with the setup button left out; passes.
- `9ad14a5` (AV06, AV07): the final tree. Only change during it: my test
  passed the transport as a trailing lambda, where the prompt goes.
- Final checks:
  - `testDebugUnitTest`: **219 JVM tests, 0 failures** (39 new);
  - `lintVitalRelease`: passes;
  - `assembleDebug`: builds;
  - `lintDebug`: 4 tool crashes (a Compose detector and Kotlin 2.1
    metadata; `TripService.kt`, untouched, is among them) and 24 warnings.
- The tested release APK sha256 is `a4b5d207…` after every run; no release
  build was made.
