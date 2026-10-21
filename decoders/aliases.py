"""Readable aliases for stored measurement keys.

Historical queries and the database use the original key (`res_pid_0149`), but
that name looks like an unresolved payload even though the decode is settled.
An alias lets a reader ask for `accelerator_pedal_pct` without renaming the
stored key, which would break every historical query.

Aliases are deliberately a *display/addressing* convenience, not a second
measurement: they resolve to one canonical key and never create a parallel
series. Add an alias only when the payload is resolved; an alias must not make
an unknown look known.
"""
from __future__ import annotations

# alias -> canonical stored key
_ALIASES: dict[str, str] = {
    "accelerator_pedal_pct": "res_pid_0149",
}

# canonical key -> aliases (stable order)
_CANONICAL: dict[str, tuple[str, ...]] = {}
for _alias, _key in _ALIASES.items():
    _CANONICAL[_key] = (*_CANONICAL.get(_key, ()), _alias)


def resolve(key: str) -> str:
    """Return the stored key for an alias, or the key unchanged."""
    return _ALIASES.get(key, key)


def aliases_for(key: str) -> tuple[str, ...]:
    """Return the aliases of a canonical key (empty if there are none)."""
    return _CANONICAL.get(key, ())


def alias_map() -> dict[str, str]:
    """A copy of the alias -> canonical map, for export and tests."""
    return dict(_ALIASES)
