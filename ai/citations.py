"""Evidence citations the AI cannot invent.

The model is asked (ai/prompts.py CITATION_RULES) to cite every claim as
key@timestamp. Asking is not checking: a model will cite a reading that was
never in its evidence as fluently as one that was. So every citation in a
report is looked up in the evidence packet the model was actually given:

* verified -- that key has a reading at that moment in the packet; the
  packet's own value is attached, so a reader sees what the evidence says,
  not what the model says it says;
* unsupported -- the key is not in the packet at all, or not at that
  moment. Flagged in the report's automatic caveats, never silently dropped.

Timestamps are compared as moments (to the second, UTC), so 10:00:00Z and
10:00:00+00:00 are the same reading. Pure functions, no I/O.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

TS_FIELDS = ("ts", "last_seen", "first_seen", "observed_at", "timestamp",
             "start_ts", "end_ts")
NAME_FIELDS = ("key", "code", "source")
CITATION = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.]*)@(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)")
_KEYLIKE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")


def moment(ts) -> str | None:
    """A timestamp as UTC to the second, or None when it is not one."""
    try:
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00")
                                    .replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def index(context) -> dict[tuple[str, str], object]:
    """(key, moment) -> value for every reading in an evidence packet.

    A reading is any object with a timestamp field; its key is its own
    key/code/source field when that names a key, or the name it is filed
    under in the object above it."""
    out: dict[tuple[str, str], object] = {}

    def walk(node, name):
        if isinstance(node, dict):
            names = {name} | {node[f] for f in NAME_FIELDS
                              if isinstance(node.get(f), str)}
            names = {n for n in names if n and _KEYLIKE.match(n)}
            for f in TS_FIELDS:
                when = moment(node.get(f)) if isinstance(node.get(f), str) else None
                if when:
                    for n in names:
                        out.setdefault((n, when), node.get("value"))
            for k, v in node.items():
                walk(v, k if isinstance(k, str) else name)
        elif isinstance(node, list):
            for v in node:
                walk(v, name)

    walk(context, "")
    return out


def check(report: str, evidence: dict[tuple[str, str], object]) -> dict:
    """Each key@timestamp in `report`, looked up in `evidence` (see index())."""
    keys = {k for k, _ in evidence}
    verified, unsupported, seen = [], [], set()
    for m in CITATION.finditer(report or ""):
        key, raw = m.group(1), m.group(2)
        when = moment(raw)
        if (key, when) in seen:
            continue
        seen.add((key, when))
        if when is not None and (key, when) in evidence:
            verified.append({"key": key, "ts": when, "value": evidence[(key, when)]})
        else:
            why = ("not a timestamp" if when is None else
                   "no reading of this key at that moment in the evidence" if key in keys
                   else "this key is not in the evidence")
            unsupported.append({"key": key, "ts": raw, "why": why})
    return {"cited": len(seen), "verified": verified, "unsupported": unsupported}


def caveats(result: dict) -> list[str]:
    """Lines for the report's automatic caveats."""
    if not result["cited"]:
        return ["The report cites no evidence as key@timestamp; nothing in it "
                "can be traced to a reading."]
    return [f"Cited {u['key']}@{u['ts']}: {u['why']} -- treat that claim as "
            "unsupported." for u in result["unsupported"]]
