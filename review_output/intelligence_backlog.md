# Navigation intelligence backlog — 2026-10-31

Branch `navigation-intelligence`, from `navigation` at `eb646d3` (local tag
`tested-apk-20261031c`: the APK the owner is testing, a copy kept in
`~/odb-scanner-tested/`). Nothing pushed. No release build is made on this
branch, so the tested APK in `~/odb-scanner-build` is never replaced; the
navigation screens and the Google Maps hand-off are not touched.

Status: `pending`, `in progress`, `done <commit>`, `blocked`, `dropped`.

| ID | Task | Status |
|---|---|---|
| IN01 | Synthetic journeys with a known truth (a year of this kind of car's driving, measured the way the collector measures it) to validate prediction methods on -- never written to a database | done `89d50c1` |
| IN02 | Validation framework: chronological, outing-level holdout; flat / linear / physics compared; MAE, RMSE, bias, by temperature, speed, length, climb; interval coverage; arrival-% error; honest "too few" | done `a8288b4` |
| IN03 | Physics-informed consumption model: drag by speed², time-based loads (auxiliaries, heating), cold penalty, cold-start warm-up; a physics prior so a handful of drives does not overfit; robust to bad drives | done `a8288b4` |
| IN04 | Realistic arrival ranges: variance by leg length (correlated part ∝ km, the rest ∝ √km), model uncertainty, unknown conditions, forecast temperature error, calibrated on held-out drives; confidence and data sufficiency | done `a7f59a7` |
| IN05 | Planner and Route page use the chosen model and ranges; safeguards (few drives, outside what was seen, stale battery reading); the flat fallback kept | done `a8288b4`, `a7f59a7` |
| IN06 | Charger search: near the route, a point, the destination, within range; each judged by detour, arrival charge, charging time on this car's curve, cost, preference, picks, data age; "add / replace this stop" through the existing picks | pending |
| IN07 | This car's charging curve, measured from DC charging samples when there are enough, else the published one | pending |
| IN08 | Planned versus actual: every recorded drive against what would have been predicted for it beforehand (no new table), and an evidence-based profile of this car (motorway, town, cold, speed bands, heating, charging) -- measured and inferred kept apart | pending |
| IN09 | Resilience: deterministic tests for GPS loss/jumps/stale fixes, restart, duplicate/stale revisions, repeated proposals, provider failure; fix what they find (logic only, no screens) | pending |
| IN10 | Explainable proposals: the evidence behind an en-route assessment and a route change (time, revision, readings and their age, model, before/after, confidence) | pending |
| IN11 | Architecture review: duplication, parity, provider separation | pending |
| IN12 | Handover | pending |
| IN13 | Per-segment route speeds (ORS steps / Mapbox annotations) for the model and the wind | not started: measured worth < noise on 22 motorway drives (review §5); matters for half-town half-motorway routes. Wind with segment speeds needs the Kotlin planner too |
| IN14 | The physics model and its ranges on the phone (Kotlin port, shared vectors) | not started: the phone plans with the flat rate; needs the model fitted from the phone's own drives or handed over from the PC |
