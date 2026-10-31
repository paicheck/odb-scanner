"""The charging networks to pick from on the Route page, most locations first.

Counted from Open Charge Map's own listings for your countries
(routing.countries, else GB): every fast CCS charging location it lists,
with the filters the planner uses -- CCS, at least the minimum power, not
private, not out of operation, not Tesla-only -- grouped by network
(operator). Only what Open Charge Map lists; not a ranking from anywhere
else. Kept 30 days per countries and power, in this process.

The phone counts the same way (android/.../route/Networks.kt). The names are
Open Charge Map's, which the planner matches networks on.
"""
from __future__ import annotations

import time

NOT_A_NETWORK = {1, 45}          # "(Unknown Operator)", "(Business Owner at Location)"
NOT_IN_USE = {100, 150, 200, 210}  # StatusTypeID: not operational, planned, removed
MAX_AGE_S = 30 * 86400
_cache: dict[tuple, tuple[float, list[tuple[str, int]]]] = {}


def names(reference: dict) -> dict[int, str]:
    """Operators' names by id, from Open Charge Map's reference data."""
    out = {}
    for o in (reference or {}).get("Operators") or []:
        try:
            i, title = int(o.get("ID")), str(o.get("Title") or "").strip()
        except (TypeError, ValueError):
            continue
        if i > 0 and title:
            out[i] = title
    return out


def count(location_lists, operator_names: dict[int, str]) -> list[tuple[str, int]]:
    """(network, locations) for compact location lists: most first, then by name."""
    from routing.providers import PRIVATE_USAGE
    counts: dict[str, int] = {}
    for locations in location_lists:
        for poi in locations or []:
            op = poi.get("OperatorID")
            if not isinstance(op, int) or op <= 0 or op in NOT_A_NETWORK:
                continue
            if poi.get("UsageTypeID") in PRIVATE_USAGE or \
                    poi.get("StatusTypeID") in NOT_IN_USE:
                continue
            name = operator_names.get(op)
            if not name or "tesla-only" in name.lower():
                continue
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def networks(ocm, countries: str, min_kw: float,
             now: float | None = None) -> list[tuple[str, int]]:
    """The list for these countries ('GB,FR'; '' counts GB), fetched once
    a month through `ocm` (providers.OpenChargeMap)."""
    now = time.time() if now is None else now
    codes = [c for c in (countries or "").split(",") if len(c) == 2][:3] or ["GB"]
    key = (tuple(codes), float(min_kw))
    hit = _cache.get(key)
    if hit and now - hit[0] < MAX_AGE_S:
        return hit[1]
    got = count([ocm.locations_in(c) for c in codes], names(ocm.reference_data()))
    _cache[key] = (now, got)
    return got
