# Navigation handover — 2026-10-31

Branch `navigation` (12 commits on `maps-voice`, which holds today's two
earlier phone commits). **Nothing pushed, merged or delivered.** The car, the
adapter and the real database were not touched. Details per step:
`navigation_progress.md`; the research and decision: `navigation_review.md`;
tasks: `navigation_backlog.md`.

## The Google Maps answer

**An external Android app cannot update a Google Maps navigation that is
already running, through any supported public interface.** Maps URLs and
the Android intents only *start* navigation (or a preview); Navigation
Connect (Preview) only *reports* on a session, one destination at a time, and
makes the driver relaunch per segment; the Navigation SDK changes
destinations only of a session running inside our own app. Sources and
quotes: `navigation_review.md` §2.

**Chosen: hybrid (option C).** ODB Scanner stays the one planner and the one
source of truth. Google Maps is a *provider* it keeps in step by revision:
when the stops change, the driver is offered the new trip -- a tap, or a
spoken yes -- with the car's position as the origin so Google Maps
*navigates* rather than previews (Google documents that difference). The
Navigation SDK (option B) would need a billing account, an API key and a
Google-only map screen (the terms forbid Google route content on our
OSM/Mapbox map); it can sit behind the same route state later (NV13).

## What changed

| Step | What | Commit |
|---|---|---|
| NV01 | `route/RouteState.kt`: revisions with reasons; stale and duplicate guards; proposals that remember what was turned down; the driver's picks; what Google Maps was handed | `560b699` |
| NV03 | Google Maps links as Google documents them, Python ⇄ Kotlin with shared vectors: `dir_action=navigate`, `avoid`, encoding, 9/3 waypoints with an honest partial hand-off (the old code dropped stops silently), 2,048 chars | `41b0227` |
| NV02/04 | A re-plan that moves a stop and that the driver did not ask for is a **proposal** (notification Switch / Keep, spoken yes, planner card); hand-offs record their revision; taps always hand over the current one | `100e8b8` |
| NV06 | Each leg's km and kWh in both planners (vectors), on the phone and the dashboard | `763cd59` |
| NV05 | Avoid this charger / use an alternative, kept for the whole trip | `feffc63` |
| NV08 | The trip survives the app being stopped: saved, re-planned from here on restart, navigation resumed | `637645f`, `842705e` |
| NV07 | Navigation screen: GPS lost/weak, en-route level, proposal with big buttons, arrival range, next stop target, driven/to go, battery vs plan, night map, zoom by turn and speed | `55d2a9c`, `befe937` |
| NV09 | Assistant: "what's changed?", answer a suggestion, avoid a stop / use an alternative / avoid a network -- each change only after the driver's yes (ConfirmGate) | `63cd2fc` |
| NV10 | Android Auto: suggestion as a two-action message, ranges, revision, "new next stop: tap to navigate" | `794af01` |
| NV11 | Dashboard: Open in Google Maps with the shared link | `cf98b4f` |

## Verified

- Python: **1,617 tests passed**, ruff clean, the 100-entry snapshot of
  `~/odb_data` and `data/` identical before and after every full run.
- Phone: **170 JVM tests, 0 failures** (bounded Gradle, `-Xmx1536m`, 2 workers,
  `timeout 1500`), including the 27 route-vector cases (now with leg energy),
  the 7 hand-off links and 3 intents shared with Python, 14 en-route cases,
  route-state, store, confirm-gate, map and status tests. Release APK built,
  signed with the usual key (not copied to a phone: none connected).

## Not verified -- needs the car or the phone

1. **Proposals on the road** (NV14): the notification buttons, the spoken
   "say yes to switch", the planner and navigation cards.
2. **Google Maps re-hand-off** from the notification and by voice, with and
   without "Display over other apps", on the Pixel's Android beta.
3. **Restore after a kill**: swipe the app away mid-trip, reopen.
4. **Android Auto**: the message template with two actions, the
   "new next stop" note, on a head unit or the DHU.
5. **Switching while the in-app navigator is mid-leg** (NV15).
6. **Night map** at dusk.

## Decisions for you

- Push / merge: `maps-voice` then `navigation` (each builds on the last), or
  `navigation` alone (it contains both).
- Whether to try the Navigation SDK later (NV13): account, billing and a
  Google-only navigation screen.

## Known limits

- Google Maps keeps its own route once handed over; ODB Scanner knows what it
  *sent*, not what Google Maps is doing (Navigation Connect could report that,
  but needs a backend and billing, and is Preview).
- Android Auto stays in the charging category: navigation on the car is
  the car's navigation app, one place at a time.
- "Add a charging stop" is by choosing an alternative near a planned stop;
  there is no free search for a charger to add.
- Leg ranges and arrival ranges still carry one drive's spread per leg (wide
  on long legs); forecast error is not in them.
