"""Generate `decoder_inventory.md` from the live registry and spec.

Read-only.  Run from the repository root:

    python docs/investigation/decoder_accuracy_audit_20261026/inventory_md.py

The table is generated rather than hand-written so the audit cannot drift from
the code it claims to describe.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from decoders.catalogue import build_catalogue  # noqa: E402
from decoders.quality import build_quality  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

OUT = os.path.join(
    ROOT, "docs/investigation/decoder_accuracy_audit_20261026",
    "decoder_inventory.md")
SPEC = os.path.join(ROOT, "spec/odb_scanner.json")


def _spec() -> dict[str, dict]:
    with open(SPEC, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {m["key"]: m for m in doc["profiles"]["meb"]["measurements"]}


def _tested_keys(catalogue) -> set[str]:
    text = ""
    for base, _dirs, files in os.walk(os.path.join(ROOT, "tests")):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name), encoding="utf-8",
                          errors="replace") as fh:
                    text += fh.read()
    return {k for k in catalogue if re.search(rf"\b{re.escape(k)}\b", text)}


def _decoder_label(entry: dict | None) -> str:
    if not entry or entry.get("decode") is None:
        return "—"
    dec = entry["decode"]
    if dec["kind"] == "special":
        return f"special:{dec['name']}"
    return (f"{'s' if dec.get('signed') else 'u'}"
            f"{8 * dec['length']}be x {dec.get('scale'):g}"
            f"/{dec.get('divisor'):g} + {dec.get('addend'):g}")


def main() -> int:
    reg = build_default_registry("meb")
    cat = build_catalogue("meb", reg)
    qual = build_quality("meb", reg)
    spec = _spec()
    tested = _tested_keys(cat)
    order = sorted(cat)

    lines = [
        "# Decoder inventory — MEB profile (2021 VW ID.3 58 kWh)",
        "",
        "Generated from the live registry, catalogue, quality layer and "
        "`spec/odb_scanner.json` by `inventory_md.py`. No vehicle was contacted.",
        "",
        f"- Measurement specs: **{len(reg.all())}**",
        f"- Catalogue keys: **{len(cat)}**",
        f"- Distinct DID numbers: **{len({s.did for s in reg.all()})}**",
        f"- Distinct (ECU, DID) associations: "
        f"**{len({(s.ecu_key, s.did) for s in reg.all()})}**",
        f"- Keys with a declared byte layout: "
        f"**{sum(1 for d in cat.values() if d.layout is not None)}**",
        f"- Keys without a declared layout: "
        f"**{sum(1 for d in cat.values() if d.layout is None)}**",
        f"- Keys named anywhere in `tests/`: **{len(tested)}**; "
        f"not named: **{len(cat) - len(tested)}**",
        "",
        "## Decode shapes",
        "",
        "| shape | keys |",
        "|---|---:|",
    ]
    shapes: dict[str, int] = {}
    for key in order:
        label = _decoder_label(spec.get(key))
        shape = label.split(" x ")[0] if label.startswith(("u", "s")) else label
        shapes[shape] = shapes.get(shape, 0) + 1
    for shape, n in sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| `{shape}` | {n} |")

    lines += [
        "",
        "## Confidence and quality",
        "",
        "| layer | value | keys |",
        "|---|---|---:|",
    ]
    for field, getter in (
        ("catalogue confidence", lambda d: d.confidence.value),
        ("verification", lambda q: q.verification.value),
        ("data kind", lambda q: q.data_kind.value),
    ):
        counts: dict[str, int] = {}
        source = cat.values() if field.startswith("catalogue") else qual.values()
        for item in source:
            value = getter(item)
            counts[value] = counts.get(value, 0) + 1
        for value, n in sorted(counts.items()):
            lines.append(f"| {field} | {value} | {n} |")

    lines += [
        "",
        "## Keys that must not feed scoring",
        "",
        "| key | DID | unit | reason |",
        "|---|---|---|---|",
    ]
    for key in order:
        q = qual[key]
        if q.use_in_scoring:
            continue
        lines.append(
            f"| `{key}` | `{spec.get(key, {}).get('did', '—')}` | "
            f"{q.unit or '—'} | {q.known_issue} |")

    lines += [
        "",
        "## DIDs that carry more than one key",
        "",
        "| DID | keys |",
        "|---|---|",
    ]
    by_did: dict[str, list[str]] = {}
    for key in order:
        did = spec.get(key, {}).get("did")
        if did:
            by_did.setdefault(did, []).append(key)
    for did, keys in sorted(by_did.items()):
        if len(keys) > 1:
            lines.append(f"| `{did}` | {', '.join(f'`{k}`' for k in keys)} |")

    lines += [
        "",
        "## Full inventory",
        "",
        "`decoder` shows the exported arithmetic (`scale/divisor/addend`) or the "
        "special decoder name. `layout` means the catalogue carries a byte "
        "layout that `validate_catalogue` checks against the live decoder. "
        "`test` means the key name appears somewhere in `tests/`, which is "
        "coverage evidence, not proof that the arithmetic is independently "
        "validated.",
        "",
        "| key | DID | ECU | unit | decoder | confidence | verification | "
        "kind | layout | test |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key in order:
        d = cat[key]
        q = qual[key]
        lines.append(
            f"| `{key}` | `{spec.get(key, {}).get('did', '—')}` | "
            f"`{spec.get(key, {}).get('ecu_key', '—')}` | "
            f"{q.unit or '—'} | `{_decoder_label(spec.get(key))}` | "
            f"{d.confidence.value} | {q.verification.value} | "
            f"{q.data_kind.value} | {'yes' if d.layout else '—'} | "
            f"{'yes' if key in tested else '—'} |")

    lines += ["", "## Keys not named in any test", ""]
    for key in order:
        if key not in tested:
            lines.append(f"- `{key}`")
    lines.append("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
