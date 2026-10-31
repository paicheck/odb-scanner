# Navigation progress — 2026-10-31

Branch `navigation` (from `maps-voice`). One entry per committed step.

## Research and review
- Read the Maps URLs, Android Maps intents, Navigation SDK (overview, route
  tokens, pricing), Navigation Connect (overview, multiple destinations) and
  the Maps Platform terms. Findings and the decision (option C, hybrid) in
  `navigation_review.md`. Definitive: no supported public interface updates a
  running Google Maps session from another app.

## NV01 — one versioned route state — `560b699`
- `route/RouteState.kt` (+ `RouteStateTest`, 11 → 12 tests): revisions with
  reasons, stale/duplicate guards, proposals with "declined" memory, picks,
  provider sync record; JSON save/restore.

## NV03 — Google Maps links as documented, shared — `41b0227`
- `routing/handoff.py` ⇄ `route/GoogleMaps.kt`, `tests/data/handoff_vectors.json`
  (7 links + 3 intents); `tests/test_handoff.py` (7). `dir_action=navigate`,
  `avoid`, `%7C`/`%2C`, 9/3 waypoints with an honest partial hand-off, 2,048
  chars. Replaces the old builder that silently dropped stops beyond nine.

## NV02 + NV04 — proposals; Google Maps tracked by revision — `100e8b8`
- `TripMonitor` keeps the RouteState; `RouteRevisions.decide` (tested):
  refresh / apply (driver asked) / propose / keep. Proposals: notification
  (Switch / Keep my plan via `TripActionReceiver`), spoken yes, planner card.
  Hand-offs from the phone's position through `MapsHandoff.open` and
  `HandoffActivity` (always the current revision), recorded in the sync state.
- Phone: 159 JVM tests, 0 failures; release build OK.

## NV06 — per-leg energy, shared — see commit "Plans: each leg's distance and energy"
- Stops gain `leg_km`/`leg_kwh`, plans `last_leg`, in both planners; route
  vectors record them (27 cases), PlannerTest compares. Phone stop cards and
  the dashboard's stops table show them.
- Python full suite 1,616 passed, ruff clean, real data unchanged; phone 159.

## NV05 — avoid / use-instead per stop, kept for the trip — `feffc63`
- Planner stop cards: "Avoid this charger", "Use" on each alternative,
  "Your charger choices … Clear". Picks go into every re-plan; on a driven trip
  they apply as a revision with the reason. `Picks.avoid/insteadOf` tested.

## NV08 — recovery after a restart — see commit "a trip survives the app being stopped"
- `route/TripStore.kt` saves destination, mode, avoid, navigating flag and the
  RouteState on every route change; `TripMonitor.restore` re-plans from the
  current position as the next revision, resumes in-app navigation, notifies
  (with a Google Maps tap when it is stale). 12 h limit. `TripStoreTest` (4).
- Phone: 164 JVM tests, 0 failures; release build OK.

## NV07 — navigation screen — see commit "Navigation screen: what needs attention…"
- Alerts strip: GPS lost/weak (fix time and accuracy now in Navigator.State),
  en-route level, proposal with 56 dp Switch / Keep buttons.
- Bottom bar: driven / to go, arrival range (red below reserve), next stop
  arrival range + target charge + minutes. Night map by solar elevation.
- `NavStatus` + `NavStatusTest` (3). Phone: 167 JVM tests, 0 failures.

## NV09 — voice — see commit "Assistant: what changed with the route…"
- Tools `get_route_changes`, `answer_suggestion`, `avoid_stop`,
  `use_alternative`, `avoid_network`; changes go through `ConfirmGate`
  (first call asks, the same change confirmed within 2 min acts; tested, 3).
  `get_trip` stops carry number, arrival range, alternatives. Picks gain
  trip-only networks to avoid. Phone: 170 JVM tests, 0 failures.

## NV10 — Android Auto — see commit "Android Auto: answer a suggested plan…"
- Proposal as a MessageTemplate with Switch / Keep my plan; revision and
  reason; arrival ranges; "new next stop: tap to navigate" when a revision
  moved the stop last sent via ACTION_NAVIGATE; NavScreen rows with arrival
  charge / target. Phone: 170 JVM tests, 0 failures; release build OK.
- Not tried on a car head unit (no emulator run: disk/RAM; the DHU needs the
  owner's setup).

## NV11 — dashboard Google Maps link — see commit "Dashboard: Open in Google Maps…"
- Route page link from `routing/handoff.trip_link` (origin, stops, destination,
  the form's avoided roads); states the limits; partial note past 9 stops.
  Test in `tests/test_handoff.py`. Python full suite 1,617 passed, ruff clean,
  real data unchanged.
