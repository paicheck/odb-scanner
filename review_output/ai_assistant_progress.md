# AI assistant progress log — 2026-10-31

## Start

- Branch `ai-assistant` from `ai-voice` (`7a53162`). `navigation` (`eb646d3`)
  and `ai-voice` unchanged; local tag `tested-ai-voice-7a53162`.
- The `ai-voice` APK the owner may be testing is the debug build from 14:55
  (sha256 `4d6dd612…`): copied to `~/odb-scanner-tested/` because a debug
  build here would overwrite it. This branch packages no APK at all.
- Investigation and the architectural problem written before any change:
  `ai_assistant_review.md` §1–2.

## Implementation

- `f8de645` reliability (AA02–AA05, the AA08 backend). Found by the tests:
  - **Breaker:** a trial in flight let a second request through, because
    "ready for a trial" and "in flight" looked the same. Fixed with a
    probing flag.
  - **Fallback chain:** adding parameters after `changesState` broke the
    trailing-lambda calls; the order was restored.
  - **My test:** it expected a warning at 67 %, below the 80 % threshold.
  - **Build cache:** a stale KSP cache from the earlier worktree builds
    stopped compilation; `kspCaches` and `generated/ksp` were cleared (no
    APK output touched).
- `64da35e` streaming, history, vehicle tools, offline answers (AA06,
  AA07, AA09, AA10). Committed and tested with the screen changes set
  aside (`git stash`): 242 tests passed.
- `4258973`: CancelTest was flaky when the interrupt beat the latch under
  load. It was a test bug; the late answer is dropped either way.
- `df021d3`: setup sections, the big Stop and Retry (AA04/AA06/AA08/AA11
  user interface).
- Final checks:
  - `testDebugUnitTest`: **242 JVM tests, 0 failures**;
  - `lintVitalRelease`: passes.
  - No APK packaged. Both saved APKs are unchanged: release `a4b5d207…`,
    `ai-voice` debug `4d6dd612…`.
- No PC (Python) file changed on this branch.
