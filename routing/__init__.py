"""EV trip planning: route energy, charging stops, arrival state of charge.

`planner` is pure arithmetic (no I/O) so it can be tested exactly and ported
to the Android app (android/.../route/Planner.kt) against the same vectors in
tests/data/route_vectors.json. `providers` talks to OpenRouteService and
OpenChargeMap; `car` reads this car's own energy figures from the database.

Nothing here talks to the vehicle.
"""
