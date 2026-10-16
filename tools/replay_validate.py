"""Offline replay / validation report. Never touches a vehicle.

    python tools/replay_validate.py                     # meb profile, both DBs
    python tools/replay_validate.py --profile eup
    python tools/replay_validate.py --out docs/investigation/replay_report.md

What it does:
  1. validates the catalogue against the live decoders (offline probe);
  2. re-decodes every stored measurement that carries bytes;
  3. classifies each observation by where it actually came from -- vehicle
     response, third-party reported value, or synthetic fixture -- so a
     simulator round-trip can never be mistaken for corroboration;
  4. for the two HV energy DIDs, reports which byte layouts are arithmetically
     capable of reproducing the only real-world values that exist;
  5. writes a Markdown report and syncs the catalogue into the database.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.replay import (  # noqa: E402
    EvidenceKind,
    Observation,
    ReplayResult,
    Verdict,
    compare,
    infer_layout,
    observations_from_measurements,
    refuted_layouts,
    to_json,
)
from decoders.catalogue import (  # noqa: E402
    ByteLayout,
    ByteOrder,
    build_catalogue,
    validate_catalogue,
)
from decoders.registry import build_default_registry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBS = ["data/real_car.db", "data/odb_scanner.db", "data/odb_scanner_cdrive_20261015.db"]
ENERGY_KEYS = ("hv_energy_content", "hv_energy_max")

# Databases whose stored values were produced by a DIFFERENT DID profile than
# the one being validated. Established empirically, not assumed: raw '103F'
# for battery_temp is 24.98 C under `eup` (u8/2-40) and -32.0 C under `meb`
# (u8/64-40), and every row in these files matches the eup arithmetic. Replaying
# them against the meb catalogue "fails" for a reason that has nothing to do
# with any definition: the bytes were never MEB bytes.
#
# 2026-09-01..09-19 in odb_scanner.db and all of odb_scanner_cdrive_20261015.db
# predate the MEB work (git: 9f584b0 / f233d6f) and were collected against the
# e-Up/OVMS map. real_car.db from 2026-10-12 onward IS meb. So only real_car.db
# is replayed against the meb catalogue by default; --all-profiles replays every
# file against both catalogues and lets the report say which combination fits.
_DB_PROFILE_HINT = {
    "data/odb_scanner.db": "eup",
    "data/odb_scanner_cdrive_20261015.db": "eup",
    "data/real_car.db": "meb",
}

# Every byte layout ever proposed for the HV energy DIDs, live or superseded,
# so the report can show which are still able to produce the observed values.
# The u32/1310.77 entries are the decoders that used to ship; they are kept here
# so their exclusion stays reproducible rather than being asserted in prose.
_SUPERSEDED_U32 = ByteLayout(offset=0, length=4, byte_order=ByteOrder.BIG_ENDIAN,
                             signed=False, scale=1.0, divisor=1310.77)
_CANDIDATES = {
    "hv_energy_content": {
        "u16be[2:4] x25 (live)": ByteLayout(offset=2, length=2,
                                           byte_order=ByteOrder.BIG_ENDIAN,
                                           signed=False, scale=25.0),
        "u32be / 1310.77 (old)": _SUPERSEDED_U32,
        "u16be[0:2] x25": ByteLayout(offset=0, length=2,
                                     byte_order=ByteOrder.BIG_ENDIAN,
                                     signed=False, scale=25.0),
    },
    "hv_energy_max": {
        "u16be[0:2] x50 (live)": ByteLayout(offset=0, length=2,
                                           byte_order=ByteOrder.BIG_ENDIAN,
                                           signed=False, scale=50.0),
        "u32be / 1310.77 (old)": _SUPERSEDED_U32,
    },
}


def _CANDIDATE_LAYOUTS(key: str) -> dict:
    return _CANDIDATES.get(key, {})


def read_observations(db_path: str) -> list[Observation]:
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect("file:{}?mode=ro".format(db_path.replace("\\", "/")), uri=True)
    con.row_factory = sqlite3.Row
    # `measurements.raw_response` holds the DID DATA RECORD -- the bytes after the
    # 0x62 marker and the echoed DID were stripped, which is exactly what the
    # decoders receive (verified: pack_voltage raw '5944' -> 357.0625 V matches
    # u16be 0x5944 / 4). So it can be fed straight back to a layout. Rows whose
    # raw_response is not hex -- imported Car Scanner export lines, or empty --
    # get raw_hex=None and therefore cannot be re-decoded, which is correct:
    # there are no bytes to re-decode.
    rows = con.execute(
        "SELECT key, ts, value, unit, raw_response, decoding_method, provenance,"
        " doc_status, ecu, service, pid FROM measurements WHERE value IS NOT NULL"
        " ORDER BY ts").fetchall()
    con.close()
    return observations_from_measurements(rows, os.path.basename(db_path))


def classify(obs: list[Observation]) -> dict[str, list[Observation]]:
    out: dict[str, list[Observation]] = {k.value: [] for k in EvidenceKind}
    for o in obs:
        out[o.evidence_kind.value].append(o)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="meb")
    ap.add_argument("--out", default="")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--sync", action="store_true",
                    help="write the catalogue into data/odb_scanner.db")
    ap.add_argument("--all-profiles", action="store_true",
                    help="replay every database against both catalogues, "
                         "including files stored under the other DID profile")
    args = ap.parse_args()

    reg = build_default_registry(args.profile)
    cat = build_catalogue(args.profile, reg)
    problems = validate_catalogue(args.profile, reg)
    results: list[ReplayResult] = []

    print("=" * 74)
    print(f"profile={args.profile}  definitions={len(cat)}")
    print("=" * 74)
    print("1. catalogue self-validation (offline probe vs live decoders)")
    print("   " + ("OK -- no problems" if not problems else "PROBLEMS:"))
    for p in problems:
        print("     !", p)

    print()
    print("2. observation inventory")
    all_obs: list[Observation] = []
    per_db: dict[str, list[Observation]] = {}
    for db in DBS:
        obs = read_observations(db)
        per_db[db] = obs
        all_obs.extend(obs)
        if not obs:
            print(f"   {db}: (absent or no valued measurements)")
            continue
        c = classify(obs)
        print(f"   {db}: {len(obs)} valued rows")
        for kind, rows in c.items():
            if rows:
                print(f"       {kind}: {len(rows)}")

    print()
    if args.all_profiles:
        print("3. replay every observation against BOTH catalogues")
        pairs = [(db, p) for db in per_db for p in ("meb", "eup")]
    else:
        print(f"3. replay of observations that carry bytes (profile={args.profile})")
        pairs = [(db, args.profile) for db in per_db
                 if _DB_PROFILE_HINT.get(db, args.profile) == args.profile]
        skipped = [db for db in per_db
                   if _DB_PROFILE_HINT.get(db, args.profile) != args.profile]
        if skipped:
            print(f"   skipped (stored under a different DID profile): "
                  f"{', '.join(skipped)} -- use --all-profiles to include them")
    by_key: dict[str, list[Observation]] = {}
    for db, _p in pairs:
        for o in per_db[db]:
            by_key.setdefault(o.key, []).append(o)
    replayed = 0
    for key in sorted(by_key):
        d = cat.get(key)
        if d is None or d.layout is None or not any(o.raw_hex for o in by_key[key]):
            continue
        r = compare(key, d.layout, d.version, by_key[key], reg)
        results.append(r)
        replayed += 1
        print(f"   {key:28s} {r.verdict.value:14s} {r.detail[:88]}")
    print(f"   ({replayed} definition(s) replayed)")

    print()
    print("4. HV energy DIDs -- what the real-world values actually constrain")
    energy_obs = [o for o in all_obs if o.key in ENERGY_KEYS]
    for key in ENERGY_KEYS:
        obs = [o for o in energy_obs if o.key == key]
        if not obs:
            print(f"   {key}: no observations at all")
            continue
        d = cat[key]
        if d.layout is None:
            print(f"   {key}")
            print("     !! catalogue has NO layout for this key -- this is a bug "
                  "in the overlay, not a finding about the vehicle")
            continue
        vals = sorted({o.value for o in obs if o.is_corroborating})
        syn = [o for o in obs if o.evidence_kind is EvidenceKind.SYNTHETIC_FIXTURE]
        with_bytes = [o for o in obs if o.raw_hex]
        print(f"   {key}")
        print(f"     corroborating values : {vals or 'NONE'}")
        print(f"     observations with bytes: {len(with_bytes)} of {len(obs)}")
        print(f"     synthetic fixtures      : {len(syn)}")
        if vals:
            inf = infer_layout(key, obs, reg, width=d.layout.length if d.layout else 2)
            results.append(inf)
            print(f"     admissible scales       : {inf.extras.get('admissible_scales')}")
            print(f"     -> {inf.detail}")
            # Test every candidate layout that has ever been proposed for this
            # DID, not just the live one. A layout that cannot reach any
            # observed value is excluded, which needs no vehicle.
            verdicts = refuted_layouts(key, _CANDIDATE_LAYOUTS(key), obs)
            for name, why in verdicts.items():
                print(f"     layout {name:22s}: {why}")
        else:
            print("     -> NO corroborating value: the definition rests entirely "
                  "on third-party definition records")
        if with_bytes:
            r = compare(key, d.layout, d.version, obs, reg)
            results.append(r)
            print(f"     replay verdict          : {r.verdict.value} -- {r.detail[:110]}")
        else:
            print("     replay verdict          : INDETERMINATE -- no observation "
                  "for this DID carries response bytes, so the layout cannot be "
                  "replayed. The stored raw_response is a Car Scanner export "
                  "line, not a payload.")
            results.append(ReplayResult(
                key, d.version, None, None, Verdict.INDETERMINATE,
                "no observation carries response bytes; raw_response holds "
                "third-party export text. Layout untested against any payload.",
                len(obs), tuple(sorted({o.evidence_kind.value for o in obs}))))

    print()
    print("5. verdict summary")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict.value] = counts.get(r.verdict.value, 0) + 1
    print("   " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "   none")
    for r in results:
        if r.verdict is Verdict.FAIL:
            print(f"   FAIL {r.key}: {r.detail[:160]}")

    if args.sync:
        from database.repository import Repository
        repo = Repository(os.path.join(ROOT, "data", "odb_scanner.db"))
        seen = repo.sync_definitions(list(cat.values()))
        print()
        print(f"6. synced {len(seen)} definitions into data/odb_scanner.db")
        repo.close()

    if args.out:
        _write_markdown(args.out, args.profile, cat, problems, results, per_db)
        print(f"   report written to {args.out}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(to_json(results))
        print(f"   json written to {args.json_out}")
    return 0


def _write_markdown(path: str, profile: str, cat, problems, results, per_db) -> None:
    lines = [
        "# Offline replay & validation report", "",
        f"Profile: `{profile}`. Generated by `tools/replay_validate.py`.",
        "No vehicle, no network, no diagnostic request was made.",
        "",
        "Re-run with `python tools/replay_validate.py`.", "",
        "## 1. Catalogue self-validation", "",
    ]
    lines.append("No problems found — every declared layout reproduces the "
                 "decoder that actually ships, with a synthetic probe record."
                 if not problems
                 else "\n".join(f"- {p}" for p in problems))
    lines += ["", "## 2. Observation inventory", "",
              "How each stored value came to exist. The distinction is the whole "
              "point: a value decoded by somebody else's software is real data "
              "about this vehicle but not a vehicle response, and a simulator "
              "round-trip is not evidence at all.", "",
              "| database | valued rows | vehicle response | third-party reported "
              "value | synthetic fixture |", "|---|---:|---:|---:|---:|"]
    for db, obs in per_db.items():
        if not obs:
            continue
        c = classify(obs)
        lines.append(f"| `{db}` | {len(obs)} | {len(c['vehicle_response'])} | "
                     f"{len(c['third-party reported value'])} | "
                     f"{len(c['synthetic fixture'])} |")
    lines += ["", "## 3. Results", "",
              "| key | verdict | records decoded | real-world replays | "
              "synthetic | max error | detail |",
              "|---|---|---:|---:|---:|---:|---|"]
    for r in results:
        e = r.extras
        detail = r.detail.replace("|", "\\|")
        if len(detail) > 170:
            detail = detail[:167] + "..."
        lines.append(
            f"| `{r.key}` | {r.verdict.value} | "
            f"{e.get('records_decoded', 0)} | {e.get('real_world_replays', 0)} | "
            f"{e.get('synthetic_records', 0)} | {e.get('max_error', '')} | {detail} |")
    lines += ["", "## 4. Confidence of the HV energy DIDs", "",
              "| key | DID | layout | confidence | limits |", "|---|---|---|---|---|"]
    for key in ENERGY_KEYS:
        d = cat.get(key)
        if not d:
            continue
        lines.append(f"| `{key}` | `{d.as_dict()['did']}` | "
                     f"{d.layout.describe() if d.layout else '—'} | "
                     f"**{d.confidence.value}** | {d.limitations[:150]} |")
    lines += ["", "Full reasoning: "
              "[`phase_c_energy_validation.md`](phase_c_energy_validation.md).", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
