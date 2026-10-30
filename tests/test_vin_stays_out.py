"""The car's VIN does not spread to new tracked files.

The repository is public, and the VIN was in config.yaml, the README, the
living docs and 18 test files. Tests now use the synthetic WVWZZZE1ZMS000001,
the car's own VIN lives in config.local.yaml (gitignored), and the generated
DATA_CATALOGUE.md masks it. What remains are the dated evidence files, which
quote captures verbatim and are cited by path; taking it out of those, and out
of history, is the owner's decision (CLAUDE.md, open items).

This test holds the VIN's SHA-256, not the VIN -- not for secrecy (the
evidence files are public), but so that this file is not one more copy. It
fails when any other tracked file contains it.
"""
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAR_VIN_SHA256 = "d44a3794b9a3b16f6899d0a23baf6fa6d2e217b19547e59877ea9e655576d252"

# Dated evidence and research records that quote the car's identity as
# captured. New files do not belong here.
EVIDENCE = {
    "docs/CAR_SCANNER_REVERSE_ENGINEERING.md",
    "docs/MEB_NO_RESPONSE_HANDOFF.md",
    "research/measurement_discovery/candidate_measurements.csv",
    "research/measurement_discovery/decoder_audit.md",
    "research/measurement_discovery/evidence_report.md",
    "research/measurement_discovery/final_report.md",
    "research/measurement_discovery/payload_research_20261021.md",
    "research/measurement_discovery/research_plan.md",
}

# A 17-character VIN: digits and capitals, never I, O or Q.
_VIN = re.compile(rb"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])")


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                             capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.decode("utf-8").split("\0") if p]


def _contains_the_car_vin(data: bytes) -> bool:
    return any(hashlib.sha256(m.group()).hexdigest() == CAR_VIN_SHA256
               for m in _VIN.finditer(data))


def test_the_car_vin_is_only_in_the_evidence_files():
    found = []
    for rel in _tracked_files():
        path = ROOT / rel
        if not path.is_file():
            continue
        if _contains_the_car_vin(path.read_bytes()):
            found.append(rel)
    unexpected = sorted(set(found) - EVIDENCE)
    assert not unexpected, (
        "the car's VIN is in these tracked files; use the synthetic "
        f"WVWZZZE1ZMS000001 in tests and config.local.yaml for the real one: "
        f"{unexpected}")


def test_the_check_recognises_a_vin():
    assert _VIN.search(b"vin: WVWZZZE1ZMS000001\n")
    assert not _VIN.search(b"WVWZZZE1ZMS0000011")        # 18 characters
    assert not _contains_the_car_vin(b"WVWZZZE1ZMS000001")
