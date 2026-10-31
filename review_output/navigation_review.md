# Navigation review — 2026-10-31

What exists, what Google's platforms genuinely allow, and the architecture
chosen. Facts about Google are from the official pages linked inline, read
on 2026-10-31; anything not stated there is marked *not documented*.

## 1. What exists (audit)

| Area | Where | State |
|---|---|---|
| Trip planner (energy, charging stops, preferences, wind, arrival ranges) | `routing/planner.py` ⇄ `android/.../route/Planner.kt` | Mature. Held together by `tests/data/route_vectors.json` (27 cases incl. wind and ranges). |
| Personal consumption model + backtest | `analysis/consumption_model.py`, `analysis/consumption_backtest.py`, `routing/car.py` | PC only; used by the Route page when it beats the flat rate. Phone plans with a flat figure. |
| En-route levels | `analysis/enroute.py` ⇄ `route/EnRoute.kt` (`enroute_vectors.json`) | Level shown in the phone's trip note; critical spoken. |
| Trip watch / re-planning | `route/TripMonitor.kt`, `route/Replan.kt` | Every minute: drift, off-route, charge finished → **re-plans and applies the new plan at once**, then announces it. |
| In-app turn-by-turn | `route/Navigator.kt`, `route/Nav.kt`, `NavigationScreen.kt` | Leg by leg (to the next stop, then on), Mapbox (traffic, lanes, speed limits) or ORS, re-route off route, traffic check every 5 min, spoken turns, heading-up OSM map, pinch zoom and overview (today). |
| Google Maps handoff | `route/MapsHandoff.kt`, `RouteScreen.startTrip`, `MapMath.googleMapsTripUrl` | Maps URL with `origin` (the trip's *original* start), waypoints (≤ 9), destination. No `dir_action`, no `avoid`. Re-plan → notification tap / spoken yes re-sends. |
| Android Auto | `car/CarScreens.kt`, `OdbCarAppService.kt` | **Charging** category: list/message/search templates. Text turn-by-turn backs up speech; `ACTION_NAVIGATE geo:` hands one destination to the car's navigation app. |
| Assistant | `ai/AssistantTools.kt` | Tools: battery, faults, trip, directions, chargers, plan, re-plan, start navigation, reroute, readings, history. |
| Dashboard Route page | `web/routes/route.py`, `route.html` (Leaflet) | Plans, shows stops with ranges, alternatives, avoid/use links, traffic ways. No hand-off to the phone or Google Maps. |

### Deficiencies found

1. **No single, versioned route state.** The active plan is whatever
   `TripMonitor.state.result` holds; a re-plan replaces it. There is no
   revision number, no record of why it changed, no record of what was sent
   to Google Maps — so the app cannot tell whether Google Maps is out of
   date, and cannot answer "what changed?".
2. **Route changes are applied without the driver's approval.** A drift
   re-plan that moves a charging stop takes effect immediately (announced
   after the fact).
3. **Driver picks are lost on re-plan.** The phone has no avoid/use picks
   per trip; the PC has them only as URL parameters.
4. **No recovery after a restart.** Process death ends the trip watch and
   navigation; nothing is persisted.
5. **Google Maps handoff is weaker than the platform allows:** the
   original start as `origin` turns a mid-trip re-send into a *route
   preview* instead of navigation (see §2.1); avoided roads are not passed;
   more than 9 stops are silently truncated.
6. **Charging stops show little of what a driver needs while driving**
   (target charge, energy of the leg, distance to the next stop).
7. **Android Auto** shows no arrival battery range and has no way to accept
   or refuse a proposed change.

## 2. Google: what is supported

### 2.1 Maps URLs ([get started](https://developers.google.com/maps/documentation/urls/get-started))

- `https://www.google.com/maps/dir/?api=1` with `origin`, `destination`,
  `waypoints` (pipe-separated, `|` encoded `%7C`), `travelmode=driving`,
  `avoid=tolls,highways,ferries`, place-ID variants, and
  `dir_action=navigate`.
- Waypoints: "up to three waypoints supported on mobile browsers, and a
  maximum of nine waypoints supported otherwise"; ignored where unsupported.
- `dir_action=navigate`: turn-by-turn **only if no origin is given or the
  origin is close to the user's current location**; otherwise a route
  preview. "Navigation is not available on all Google Maps products."
- 2,048 characters per URL. No API key.
- Nothing about updating or controlling a running navigation session.

### 2.2 Android intents ([Google Maps intents](https://developer.android.com/guide/components/google-maps-intents))

- `google.navigation:q=lat,lng&mode=d&avoid=tf` starts turn-by-turn from
  the current location to **one** endpoint. Waypoints: *not documented*.
- `geo:` intents show or search a place.
- `setPackage("com.google.android.apps.maps")`; check `resolveActivity()`
  first — "your app might crash" if nothing handles the intent.
- Behaviour when sent during a running session, and any control of an
  existing session: *not documented*.

### 2.3 Navigation Connect API (Preview) ([overview](https://developers.google.com/maps/documentation/navigation/connect/overview), [multiple destinations](https://developers.google.com/maps/documentation/navigation/connect/multiple-destinations))

- The driver navigates in the Google Maps (Android) or Waze app; your
  **server** receives telemetry (location, ETA, remaining route) through a
  trip token appended to the launch link. REST: `trips.create`, `trips.get`
  only.
- "Tracks a single route to a single destination"; more than one waypoint
  in the launch URL is an error. For each next destination the driver taps
  back to your app and launches the next segment.
- Not Android Auto, not iOS, not web. Needs a Google Cloud project,
  billing, and a backend.
- It **reads from** Google Maps; it does not **write to** it.

### 2.4 Navigation SDK for Android ([overview](https://developers.google.com/maps/documentation/navigation/android-sdk), [routes](https://developers.google.com/maps/documentation/navigation/android-sdk/customize-route), [pricing](https://developers.google.com/maps/documentation/navigation/android-sdk/pricing))

- Google's turn-by-turn **inside our app**. `Navigator.setDestinations`
  takes up to 25 waypoints and "overrides any previously-set destinations"
  — our app owns the session and can change it.
- Route tokens (from the Routes API) carry "the route polyline and route
  objectives", for one origin/destination at one time; request a new one
  every time destinations are set; with a token the SDK ignores the app's
  avoid options. No EV or charging-stop routing is documented.
- Billing: Navigation Request SKU, per destination; first 1,000
  destinations a month free, then $25 per 1,000 (tiers fall with volume).
  Needs an API key on a billing-enabled Google Cloud project.
- Android Auto: documented ("Enable navigation for Android Auto"), as a
  navigation-category app with Google's map.
- **Terms:** Google Maps content (Routes, Directions, Navigation SDK …)
  must not be used "in conjunction with a non-Google map"; the general
  terms (3.2.3(e)) forbid use "with or near a non-Google Map"
  ([service terms](https://cloud.google.com/maps-platform/terms/maps-service-terms),
  [terms](https://cloud.google.com/maps-platform/terms)). Our OpenStreetMap
  map, Mapbox routes and Google routes cannot share a screen.

### 2.5 The definitive answer

**An external Android app cannot update an already-running Google Maps
navigation session through any supported public interface.** Maps URLs and
intents *start* navigation (or a preview); Navigation Connect only *reports*
on a session, one destination at a time, and has the driver relaunch for
each new one; the Navigation SDK changes destinations only of a session
running *inside our own app*. A changed route reaches Google Maps only as a
**new hand-off that the driver accepts** — which is also what Android's
background-activity rules require (a notification tap, or the driver's
"Display over other apps" permission).

## 3. Options and the decision

| | A: Google Maps hand-off | B: Navigation SDK in the app | C: Hybrid (chosen) |
|---|---|---|---|
| Who navigates | Google Maps | Google, inside ODB Scanner | ODB Scanner's navigator, or Google Maps on request |
| Dynamic updates | Only by a new hand-off the driver accepts | Yes (`setDestinations`) | Yes in-app; Google Maps by accepted re-hand-off |
| Energy / charger intelligence | Ours, but Google Maps cannot show it | Ours as waypoints only; Google map required | Ours throughout |
| Cost / accounts | None | Billing account, API key, Google terms | None (Mapbox token optional, as now) |
| Map | Google's | Google's only (terms) | OSM/Mapbox in-app; Google's when handed off |
| Android Auto | Google Maps on the car | Possible as a navigation app | Car's navigation app via `ACTION_NAVIGATE`; our charging screens |

**Chosen: C.** It keeps one authoritative planner (ours, which knows the
car), works without accounts or charges, and treats Google Maps as a
*provider* the app keeps in step by revision: each new revision that
changes where the car must go marks Google Maps out of date, and the driver
is offered a one-tap (or spoken "yes") re-hand-off with the current position
as origin, so Google Maps starts navigating rather than previewing. Option B
stays possible behind the same provider interface if the owner ever wants
Google's own guidance in-app and accepts the account and terms; it would
need its own Google-only navigation screen.

## 4. Architecture introduced

- **`route/RouteState.kt` — the single authoritative route state** inside
  `TripMonitor.state`: trip id, revision number, ordered stops and
  destination of each revision with its reason, time and arrival charge;
  the driver's picks (avoided / chosen chargers) that every re-plan keeps;
  a **pending proposal** (a re-plan that would change where the car goes,
  waiting for the driver); and the **provider sync record** (which revision
  Google Maps has, when, and whether it is current, stale or failed).
- Re-plans are computed against a revision; a result computed against an
  older revision is refused (stale), an identical one is not a new
  revision, so nothing is applied twice.
- Google Maps is never a source of truth: what it was sent is recorded, it
  never feeds energy or arrival figures back.

The rest is in `navigation_progress.md` (what was built and tested) and
`navigation_handover.md`.
