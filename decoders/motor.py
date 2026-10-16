"""Electric drive decoders.

STATUS (2026-10-16): drive-system DIDs (motor torque, motor speed, inverter
losses, rotor/stator/PCB temperatures, DC-link voltage/current) are now
registered in decoders/meb.py under the "eldrive" ECU (MEB module 0x7C,
29-bit 0x17FC007C -> 0x17FE007C), recovered verbatim from the Car Scanner MEB
profile (profile_EV_MEB_*.json). They are EXPERIMENTAL -- not yet confirmed by
this project's own capture -- but the scalings come from Car Scanner's own
profile, not a guess.

This module is deliberately empty: the "eldrive" ECU belongs to the MEB map,
so its decoders live in meb.py. Do not duplicate them here.
"""
from __future__ import annotations

from .registry import DIDRegistry  # noqa: F401  (import surface for future use)
