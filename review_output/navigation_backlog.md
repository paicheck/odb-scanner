# Navigation backlog — 2026-10-31

Branch `navigation` (from `maps-voice`). Pushed 2026-10-31, not merged. Status: `pending`,
`in progress`, `done <commit>`, `blocked`, `dropped`.

| ID | Task | Status |
|---|---|---|
| NV01 | Canonical route state: revisions with reasons, stale/duplicate guards, provider sync record, driver picks kept across re-plans (`route/RouteState.kt`) | done `560b699` |
| NV02 | Route changes as proposals: a re-plan that moves a stop or the destination waits for the driver (phone, notification, voice, car screen); figures-only updates apply | done `100e8b8` |
| NV03 | Google Maps hand-off v2, shared Python/Kotlin with vectors: current position as origin, `dir_action=navigate`, `avoid`, encoding, 9/3 waypoint limits with an honest partial hand-off, 2,048-char limit, not-installed fallback | done `41b0227` |
| NV04 | Sync: hand-off records the revision sent; later revisions mark Google Maps stale only when where-to-go changed; no duplicate prompts | done `100e8b8` |
| NV05 | Charging-stop actions on the phone: avoid this charger, use an alternative, keep picks for the trip | done `feffc63` |
| NV06 | Per-leg energy, distance to next stop and target charge in both planners (vectors) and on screen | done `763cd59` |
| NV07 | Navigation screen: arrival charge with range, next stop target, en-route warning banner, GPS-lost indicator, night map | done `55d2a9c, befe937` |
| NV08 | Recovery: persist the trip's intent and route state; restore after restart with a fresh re-plan | done `637645f, 842705e` |
| NV09 | Voice: "what's changed?", "find another charger", "avoid this charger/network" — changes need the driver's yes | done `63cd2fc` |
| NV10 | Android Auto: arrival range, next stop, accept/keep a proposed change, revision status | done `794af01` |
| NV11 | Dashboard: Google Maps link (shared builder), per-leg energy | done `cf98b4f` |
| NV12 | Handover and docs | done |
| NV13 | Navigation SDK provider behind the same RouteState (option B), if the owner wants Google's guidance in-app | on hold (owner, 2026-10-31): a second opinion first; needs a billing account and a Google-only navigation screen |
| NV14 | Try it in the car: proposals, Google Maps re-hand-off, restore after a kill, Android Auto message template, night map | owner |
| NV15 | A suggested plan's "Switch" while the in-app navigator is mid-leg re-routes to the new stop; checked by reading, not on the road | owner |
