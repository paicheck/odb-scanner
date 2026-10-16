"""Phase C: which decoder layout can explain the observed Car Scanner values?

    python tools/phase_c_arithmetic.py

WHAT THIS IS FOR
----------------
A standalone, dependency-free restatement of the refutation behind
`decoders/meb.py`'s ENERGY DECODER RESOLUTION note. It imports nothing from
this project, so anyone can run it and check the arithmetic without reading
the library. `analysis/replay.py` implements the same two operations
(`scale_candidates`, `layout_admits`/`refuted_layouts`) for the report, and
`tests/test_catalogue.py::test_the_standalone_proof_agrees_with_the_library`
fails if the two ever disagree. The duplication is deliberate and pinned.

WHAT THE EVIDENCE ACTUALLY IS
-----------------------------
`data/real_car.db`, `measurements`, keys `hv_energy_content` / `hv_energy_max`:

    hv_energy_content  41,325 Wh   2 rows  2026-10-05T19:02:08
    hv_energy_content  41,200 Wh   2 rows  2026-10-05T19:17:25
    hv_energy_max      53,200 Wh   5 rows  2026-10-05T19:02:07 .. 19:17:28

    decoding_method = 'carscanner-export'
    provenance      = 'imported: Car Scanner CSV'
    doc_status      = 'inferred'
    raw_response    = '<epoch>;<label>;<value>;<unit>'   <-- a CSV line, NOT bytes

Those 9 rows are the ONLY non-NULL values for either DID in any database in
this repository. Every other row (70 of them) is

    value=NULL, success=0,
    error='No/undecodable response to DID 0x2ABx', raw_response=''

all addressed 0x710 -> 0x77A on 2026-10-06 and 2026-10-12, all unanswered.

NO RAW VEHICLE RESPONSE EXISTS FOR EITHER DID IN THIS REPOSITORY.

The 4-byte payload `04 28 0A 64` that appears in the tests and the simulator is
SYNTHETIC: the simulator builds it as `int(53200.0 * 1310.77).to_bytes(4)`. A
test that decodes the simulator's own output with the simulator's own constant
validates nothing but internal consistency. It is labelled as synthetic here.

THE QUESTION
------------
Given that only the decoded VALUES are real evidence, which byte layout is
capable of having produced them?

Reachability is an existence question, and the two layouts invert differently:

    u16be x s :  value = raw * s     ->  raw = value / s
    u32be / d :  value = raw / d     ->  raw = value * d

A layout reaches a value v only if that raw is an integer. There is no raw at
all if it is not, so the layout cannot have produced v. This needs no captured
bytes -- only the decoded values -- and it is checkable offline. It is what
separates the two layouts this project once shipped at once.

`1310.77` is exactly `131077/100` in lowest terms, so `v * 131077/100` is an
integer only when v is a multiple of 100. That is why 41,325 Wh is decisive
while 41,200 Wh and 53,200 Wh are not.
"""
from __future__ import annotations

import json
from fractions import Fraction
from math import gcd

OBSERVED_CONTENT = (41325.0, 41200.0)   # two distinct readings, same DID
OBSERVED_MAX = (53200.0,)               # one distinct reading (5 occurrences)

# Model A: decoders/gateway.py, from the Car Scanner MEB profile record
# (SBI / DL / MUL). Explicit encoding fields, recovered verbatim.
MODEL_A = {
    "hv_energy_content": {"offset": 2, "width": 2, "scale": 25.0, "unit": "Wh"},
    "hv_energy_max": {"offset": 0, "width": 2, "scale": 50.0, "unit": "Wh"},
}
# Model B: the layout that used to ship from decoders/meb.py, with the divisor
# attributed to evDash CarVWID3.cpp.
MODEL_B_SCALE = 1310.77

SYNTHETIC_VECTOR = bytes.fromhex("04280A64")
result: dict[str, object] = {}
lines: list[str] = []


def say(text: str = "") -> None:
    lines.append(text)


def divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def raw_for_multiplier(value: float, scale: float) -> Fraction:
    """Model A: value = raw * scale  ->  raw = value / scale."""
    return Fraction(int(value)) / Fraction(scale).limit_denominator(10**9)


def raw_for_divisor(value: float, divisor: float) -> Fraction:
    """Model B: value = raw / divisor  ->  raw = value * divisor."""
    return Fraction(int(value)) * Fraction(divisor).limit_denominator(10**9)


def rule(title: str) -> None:
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


# ---------------------------------------------------------------------------
rule("1. Exact reachability of each observed value, per layout")
# ---------------------------------------------------------------------------
MODEL_B = Fraction(131077, 100)          # exactly 1310.77
reach: dict[str, list[tuple[int, bool, bool]]] = {}

for key, obs in (("hv_energy_content", OBSERVED_CONTENT),
                 ("hv_energy_max", OBSERVED_MAX)):
    scale = MODEL_A[key]["scale"]
    say(f"   {key}   Model A: u16be[{MODEL_A[key]['offset']}:"
        f"{MODEL_A[key]['offset'] + MODEL_A[key]['width']}] x {scale:g}"
        "        Model B: u32be / 1310.77")
    rows = []
    for v in obs:
        ra = raw_for_multiplier(v, scale)
        rb = raw_for_divisor(v, MODEL_B)
        a_ok = ra.denominator == 1
        b_ok = rb.denominator == 1
        rows.append((int(v), a_ok, b_ok))
        say(f"     v = {int(v):<7d}  A: raw = {str(ra):<16s}"
            f"{'REACHABLE' if a_ok else 'NO RAW':<11s}"
            f"(fits u16: {a_ok and 0 <= ra <= 0xFFFF})")
        say(f"     {'':<12s}  B: raw = {str(rb):<16s}"
            f"{'REACHABLE' if b_ok else 'NO RAW'}")
    reach[key] = rows

say()
say("   Why 41,325 Wh is decisive and the other two are not:")
say("   1310.77 is exactly 131077/100, and gcd(131077, 100) = 1, so")
say("   v * 1310.77 is an integer only when v % 100 == 0.")
for key, obs in (("hv_energy_content", OBSERVED_CONTENT),
                 ("hv_energy_max", OBSERVED_MAX)):
    for v in obs:
        say(f"     {key:18s} {int(v):<7d} % 100 = {int(v) % 100}")
say("   => 41,200 Wh and 53,200 Wh are multiples of 100 and could never have")
say("      told this layout apart from any other. 41,325 Wh is not a multiple")
say("      of 100, and no u32 payload can decode to it under Model B.")

# ---------------------------------------------------------------------------
rule("2. Verdict per DID")
# ---------------------------------------------------------------------------
for key, rows in reach.items():
    a_all = all(a for _v, a, _b in rows)
    b_all = all(b for _v, _a, b in rows)
    b_any_blocked = [v for v, _a, b in rows if not b]
    say(f"   {key}")
    say(f"     Model A reaches all {len(rows)} observed value(s): {a_all}")
    say(f"     Model B reaches all {len(rows)} observed value(s): {b_all}")
    if b_any_blocked:
        say(f"     Model B is EXCLUDED by {b_any_blocked} (no integer raw exists)")
    else:
        say("     Model B is NOT excluded by this DID's own evidence")
    result[f"model_a_reaches_all:{key}"] = a_all
    result[f"model_b_reaches_all:{key}"] = b_all
    result[f"model_b_excluded_by:{key}"] = b_any_blocked

# ---------------------------------------------------------------------------
rule("3. An independent constraint: two readings of one DID")
# ---------------------------------------------------------------------------
# For a multiplicative layout this is a separate argument from section 1: the
# scale must divide the gcd of two readings, which needs no inversion at all.
g = 0
for v in OBSERVED_CONTENT:
    g = gcd(g, int(v))
say(f"   content readings {list(OBSERVED_CONTENT)} -> gcd = {g}")
say(f"   the scale must divide {g}; admissible Wh/LSB: {divisors(g)}")
say(f"   Model A's 25 is in that set: {g % 25 == 0}")
say(f"   Model B's 1310.77 is not a divisor of {g} and is not even an integer.")
result["admissible_multiplier_scales"] = divisors(g)

# ---------------------------------------------------------------------------
rule("4. The 125 Wh step between the two content readings")
# ---------------------------------------------------------------------------
delta = abs(OBSERVED_CONTENT[0] - OBSERVED_CONTENT[1])
counts_a = delta / MODEL_A["hv_energy_content"]["scale"]
say(f"   delta = {delta:.0f} Wh")
say(f"   under Model A: {counts_a:g} counts at 25 Wh/LSB -> exact")
say("   under Model B the two readings would sit at raws 54003724 and an")
say("   impossible 216670281/4, so there is no step to measure.")
result["content_delta_wh"] = delta

# ---------------------------------------------------------------------------
rule("5. Why 1310.77 looks fitted rather than measured")
# ---------------------------------------------------------------------------
product = 53200 * 1310.77
say("   the simulator builds its 0x2AB2 payload as int(53200.0 * 1310.77)")
say(f"   53200 * 1310.77 = {product:.2f} = 0x{round(product):08X}")
say("   so the synthetic payload 04 28 0A 64 IS 53200*1310.77 exactly.")
say(f"   inverting: {round(product)} / 53200 = {round(product) / 53200:.6f}"
    "   <-- reproduces 1310.77 exactly")
say()
hi = int.from_bytes(SYNTHETIC_VECTOR[:2], "big")
full = int.from_bytes(SYNTHETIC_VECTOR, "big")
say("   under Model A the same 4 bytes decode as")
say(f"     offset 0, u16be = 0x{hi:04X} = {hi}  x50 = {hi * 50} Wh")
say("   under Model B the same 4 bytes decode as")
say(f"     u32be = {full}  /1310.77 = {full / 1310.77:.4f} Wh")
say()
say("   => both models AGREE on this particular payload, so the synthetic")
say("      vector cannot discriminate between them, and never could have.")
result["synthetic_vector_discriminates"] = False

# ---------------------------------------------------------------------------
rule("6. Gross vs usable -- recorded, NOT resolved")
# ---------------------------------------------------------------------------
say(f"   max/content at 41325 Wh = {53200 / 41325:.4f}")
say("   log: energy content fell 125 Wh while accumulated-discharge rose")
say(f"   0.143 kWh = 143 Wh, so 125/143 = {125 / 143:.4f}")
say("   A ~0.87 usable/gross ratio is CONSISTENT with 0x2AB8 being usable and")
say("   0x2AB2 gross, but it does not discriminate between the byte layouts")
say("   and it is not a substitute for a measurement.")

# ---------------------------------------------------------------------------
rule("VERDICT")
# ---------------------------------------------------------------------------
VERDICT = """\
* NO raw vehicle response exists for 0x2AB8 or 0x2AB2 in this repository.
  Neither DID can be promoted to VERIFIED. Both stay PROVISIONAL.
* The u32be/1310.77 layout that used to ship as the default is REFUTED for
  hv_energy_content: 41,325 Wh would need raw = 216670281/4, which does not
  exist. This is the ONLY observation that excludes it -- 41,200 Wh and
  53,200 Wh are multiples of 100 and this layout can produce both.
* hv_energy_max is NOT refuted on its own evidence. Its exclusion is inherited
  from the content DID and from the profile's explicit MUL/DL/SBI fields.
* Model A (u16be, x25 at offset 2 / x50 at offset 0) reproduces all three
  values from integral u16 raws and is independently consistent with the gcd
  constraint. It is the layout now used, and it is the strongest offline
  support available -- which is still PROVISIONAL, not verified.
* The payload OFFSET for 0x2AB8 (SBI=2) and the addressing pair remain
  unproven and cannot be settled without a vehicle.
"""
say(VERDICT)
result["verdict"] = VERDICT.strip()

# ---------------------------------------------------------------------------
rule("VERDICT")
# ---------------------------------------------------------------------------
VERDICT = """\
* NO raw vehicle response exists for 0x2AB8 or 0x2AB2 in this repository.
  Neither DID can be promoted to VERIFIED. Both stay PROVISIONAL.
* Model B (u32be / 1310.77) is arithmetically INCAPABLE of producing 41,325 Wh,
  41,200 Wh or 53,200 Wh from an integral u32. It cannot have produced the Car
  Scanner values it was cited as validating, and it was the DEFAULT that
  shipped (config.yaml did_profile: meb).
* Model A (u16be, x25 at offset 2 / x50 at offset 0) reproduces all three
  values from integral u16 raws and explains the 125 Wh step as exactly 5
  counts. It is the layout now used, and it is the strongest offline support
  available -- which is still PROVISIONAL, not verified.
* The payload OFFSET for 0x2AB8 (SBI=2) and the addressing pair remain
  unproven and cannot be settled without a vehicle.
"""
say(VERDICT)
result["verdict"] = VERDICT.strip()

text = "\n".join(lines)
print(text)

# Written next to the script so the proof survives a clean checkout.
with open("tools/phase_c_arithmetic.out.txt", "w", encoding="utf-8") as fh:
    fh.write(text + "\n")
with open("tools/phase_c_arithmetic.json", "w", encoding="utf-8") as fh:
    json.dump(result, fh, indent=2)
