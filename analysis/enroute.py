"""En-route deviation assessment for EV journeys.

Pure functions, no I/O: given the planned prediction and the actual battery
state, classify how far reality has drifted from the plan and suggest an
action. Never presents a predicted SoC as guaranteed.
"""
from __future__ import annotations

LEVELS = ("normal", "warning", "adjust", "critical")


def assess_deviation(planned_arrival_pct: float, actual_pct: float,
                     predicted_now_pct: float, remaining_km: float,
                     reserve_pct: float = 10.0) -> dict:
    """Classify the drift between planned and actual battery state.

    planned_arrival_pct: predicted SoC at the destination/next charger when the
        trip was planned.
    actual_pct: current battery SoC.
    predicted_now_pct: what the plan predicted at this point in the journey.
    remaining_km: distance left to the next charger/destination.
    """
    deviation = actual_pct - predicted_now_pct
    # Projected arrival if the current drift persists (linear in remaining km).
    # We don't know future consumption, so this is an extrapolation, not a
    # guarantee.
    if remaining_km > 0:
        # crude: assume the drift per km already observed continues; without a
        # rate we just carry the current deviation forward.
        projected_arrival = planned_arrival_pct + deviation
    else:
        projected_arrival = actual_pct
    projected_arrival = max(0.0, min(100.0, projected_arrival))

    if projected_arrival < reserve_pct + 5 or deviation < -10:
        level = "critical"
        action = "Find a charger now; do not rely on the planned stop."
    elif deviation < -5:
        level = "adjust"
        action = "Consider an earlier charging stop; consumption is higher than planned."
    elif deviation < -2:
        level = "warning"
        action = "Monitor consumption; a small buffer remains."
    else:
        level = "normal"
        action = "Continue as planned."

    return {
        "level": level,
        "deviation_pct": round(deviation, 1),
        "projected_arrival_pct": round(projected_arrival, 1),
        "reserve_pct": reserve_pct,
        "remaining_km": remaining_km,
        "recommended_action": action,
    }
