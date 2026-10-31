# 03 Route guidance roadmap

## 1. Current routing architecture (verified)

- `routing/planner.py`: pure functions, no I/O. Energy model, charging integration, DP stop selection, assumptions.
- `routing/providers.py`: ORS route/elevation, OCM chargers, Mapbox traffic.
- `routing/car.py`: consumption from DB.
- `routing/prices.py`: own prices + OCM text.
- `web/routes/route.py`: form parsing, planning, rendering; pick links.
- Android `Planner.kt` parity via `route_vectors.json`.

Strengths: deterministic, testable, shared. Limitations: static plan, flat consumption, no en-route recalculation, no weather, no uncertainty, charger availability unknown.

## 2. Target architecture

Split into layers:

1. **Domain model** (shared Python/Kotlin): Route, EnergyModel, ChargingModel, Plan, Stop, Alternative, Uncertainty.
2. **Providers**: routing (ORS/Mapbox/HERE), chargers (OCM + network APIs), weather, traffic, speed limits.
3. **Planning engine**: energy-aware A*/DP with alternatives and contingency.
4. **Runtime guidance**: en-route recalculation using actual SOC/consumption/location.
5. **Presentation**: web + Android + Android Auto.

Keep pure calculation in shared core; I/O in providers; UI in apps.

## 3. Energy modelling improvements

- **Personalised consumption**: coefficients for temperature, speed, elevation, HVAC, road type; learned from trips with confidence and fallback.
- **Speed-dependent**: consumption vs. speed curve; traffic average speed.
- **Weather**: temperature, wind (head/tail), precipitation (rolling resistance); use weather API (Open-Meteo free, Met Office for UK).
- **HVAC**: estimate heating/cooling energy from ambient and cabin target; battery conditioning.
- **Elevation**: use ORS/Mapbox elevation; better gradient handling; regen cap at full battery.
- **Uncertainty**: provide low/expected/high arrival SOC bands; conservative vs. optimistic modes.
- **Historical familiarity**: adjust for known roads/routes.

## 4. Arrival SOC prediction

- Predict arrival SOC at destination and each charger with uncertainty interval.
- Inputs: distance, elevation profile, consumption model, weather, traffic speed, initial SOC, battery temperature.
- Output: expected + 10th/90th percentile; never present as guaranteed.

## 5. Charging optimisation

- Recommended departure SOC based on next leg + reserve + uncertainty.
- Charging time from actual curve (learned) capped by charger; availability confidence.
- Network preferences, real pricing, membership/roaming costs.
- Detour time included; contingency chargers.
- Risk assessment: arriving very low SOC, charger reliability, single-charger dependency.

## 6. Charger contingency handling

- For each planned stop, compute 2-3 alternatives within radius.
- If a charger is occupied/slow, switch to alternative with cost/time delta.
- Maintain a "plan B" chain; surface confidence.

## 7. Turn-by-turn guidance

- Use provider manoeuvres (ORS/Mapbox) with distance-to-manoeuvre, street names, exit numbers.
- Multi-stage instructions for complex junctions/roundabouts; do not invent lane data.
- Voice guidance (Android TTS) and Android Auto templates.
- Visual route highlighting and upcoming charger alerts.

## 8. UK road complexity

- Roundabouts: exit identification from manoeuvre text; spiral/multi-lane handled via provider instructions, not invented lane data.
- Motorway junctions/exits: use road numbers and exit identifiers from provider.
- Rural/urban: adapt verbosity; avoid excessive detail.

## 9. Traffic and weather integration

- Mapbox traffic for today's conditions; ORS for base.
- Weather-aware consumption; wind; temperature.
- Road closures/incidents: use provider data; recalculation.

## 10. En-route recalculation

- Continuously compare actual SOC/consumption/location to plan.
- Detect deviation; classify: normal / warning / adjust / critical contingency.
- Suggest alternatives; never imply guaranteed SOC.
- Handle deviations, missed chargers, traffic.

## 11. Android Auto

- Templates limit detail; prioritise next manoeuvre, distance, arrival SOC, charger.
- Voice announcements; charging stop screens.
- Offline/degraded: cache last plan; basic nav without network.

## 12. Voice guidance

- Android TTS; concise prompts; charger arrival announcements.

## 13. Offline/degraded operation

- Cache route/plan; allow viewing; recalculation when online.

## 14. UI wireframe descriptions

- Web: route comparison view (map + elevation + SOC band + stops + alternatives), scenario selector (fastest/cheapest/conservative), export.
- Android: nav screen with next manoeuvre card, SOC band, charger card, voice toggle.
- Android Auto: simplified cards.

## 15. Data requirements

- Trip segments with weather/speed/elevation; charging sessions; charger reliability; user preferences.

## 16. Implementation phases

- Phase A: Uncertainty bands + personalised consumption (shared).
- Phase B: En-route recalculation skeleton.
- Phase C: Charger contingency + availability confidence.
- Phase D: Android parity + navigation UX.
- Phase E: Weather/traffic integration.

## 17. Provider research notes (assumptions to verify)

- OpenRouteService: free tier, rate limits, elevation; manoeuvres available.
- Mapbox: traffic, alternatives, speed limits; cost.
- Weather: Open-Meteo (free), Met Office (UK, paid).
- Charging availability: OpenChargeMap has no live availability; network APIs (Ionity, Gridserve) vary; ABRP/PlugShare have availability but licensing/cost.
- Speed limits: Mapbox maxspeed; OSM maxspeed; UK data quality variable.
