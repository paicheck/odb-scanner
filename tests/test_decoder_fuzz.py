"""Whatever bytes come back, decoding never takes the collector down.

Nearly every decoder raises IndexError on a response shorter than its
layout -- by design: DIDSpec.decode_value is where that is contained
(logged with the raw bytes, value None, the raw response kept). This pins
the containment for every decoder of both profiles, on short, empty and
random responses, and that no decoder turns random bytes into NaN or
infinity (which would pass a range check and poison an average).
Seeded, so a failure reproduces.
"""
import logging
import math
import random

import pytest

from decoders.registry import build_default_registry

LENGTHS = list(range(0, 10)) + [16, 33, 64, 255]


def _numbers(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from _numbers(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _numbers(x)
    elif isinstance(v, float):
        yield v


@pytest.mark.parametrize("profile", ["meb", "eup"])
def test_no_response_can_crash_decoding_or_yield_nan(profile, caplog):
    rng = random.Random(f"fuzz-{profile}")
    specs = [s for s in build_default_registry(profile).all() if s.decode]
    assert len(specs) > 100
    caplog.set_level(logging.ERROR, logger="decoders.registry")   # WARNINGs expected
    for spec in specs:
        for n in LENGTHS:
            for _ in range(3):
                raw = bytes(rng.randrange(256) for _ in range(n))
                value = spec.decode_value(raw)           # must not raise
                bad = [x for x in _numbers(value) if not math.isfinite(x)]
                assert not bad, f"{spec.key} turned {raw.hex()} into {bad}"
