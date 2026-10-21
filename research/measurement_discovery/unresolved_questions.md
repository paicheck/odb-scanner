# Unresolved questions

These are questions the current evidence cannot answer. Each has a proposed
test or a reason it remains blocked. No live work is approved.

## Decoder questions

1. **Why does Car Scanner display positive pack current while the car is
   parked and `0x035E` is negative?**
   - Current evidence: profile applies `* -1`; `0x1E3D`, `0x1E3E`, `0x035E`
     are all negative at rest; discharge counter rises.
   - Provisional answer: negative = discharge; Car Scanner negates for display.
   - Test: controlled charge/discharge, all three signals, same timestamps.

2. **Is the `0x1E32` kWh counter at offset 8 or 12 for charge/discharge?**
   - Current evidence: profile and spot2000 agree; internal Ah × voltage
     arithmetic supports it.
   - Remaining: confirm direction over a known charge and drive.
   - Test: before/after a known charge and a known drive.

3. **Are the cell/sensor indices always 1-based?**
   - Current evidence: 28/31 max cell, 29/31 min cell, 31/31 max temp, 30/31
     min temp in tie-aware comparisons.
   - Remaining: the mismatches are timing/ties, not proof of a second
     convention.
   - Test: simultaneous full sweep + aggregate read; repeat at rest.

4. **What is the correct CO2 field/width?**
   - Current evidence: byte1 ×100 = 25,400 ppm; u16 = 1,022 ppm.
   - Test: fresh air, closed cabin, occupants; expect 400–1,500 ppm.

5. **What is the correct windshield temperature field/scale?**
   - Current evidence: five-byte payload; current decode ~488,500 °C; humidity
     at byte 4 is consistent.
   - Test: cold soak, defroster, ambient comparison; log all five bytes.

6. **Is `0x5867` ×10, ×100, or a different counter?**
   - Current evidence: ×10 = 11,150 km; ×100 = 111,500 km; other odometers
     ~111,020 km.
   - Test: known-distance drive; compare increments.

7. **Why is `bat_charging_voltage` 0 or `0xFFFF`?**
   - Current evidence: samples are 0 or sentinel; not usable as a voltage.
   - Test: measure during actual charging; identify sentinel.

8. **What is `dc_dc_converter_reserved_current`?**
   - Current evidence: 243–252 A; impossible for a DC-DC current.
   - Test: identify field/offset; do not use.

9. **Are `cell_soc_max/min` values correct?**
   - Current evidence: text JSON gives 81.67%/80.20% with indices; numeric
     column null.
   - Test: promote to numeric; compare with per-cell SOC/voltage.

10. **Does the `0x1E32` block ever return fewer than 16 bytes?**
    - Current evidence: 16 bytes in samples; decoder historically accepted 8.
    - Test: short/truncated payload handling; no silent 0.

## New-signal questions

11. **What are gateway `0x2A26`, `0x2A28`, `0x2A2F`?**
    - Current evidence: positive `0x62` in passive capture; no payload.
    - Blocked by: original btsnoop log not in workspace.
    - Next: recover capture; extract request/response/length.

12. **Do ABS wheel speeds respond on this car, and are they worth the
    `0x10 03` session?**
    - Current evidence: profile + passive positive response; no values.
    - Blocked by: session-change approval.
    - Next: separate high-risk test; all four wheels at crawl.

13. **Is the `0x2430` GPS layout stable across model years?**
    - Current evidence: evDash parser + one sample payload; no vehicle read.
    - Test: outdoor fix; compare with phone GPS; verify multiframe.

14. **Is `0x22B3` time authoritative?**
    - Current evidence: evDash bit masks only.
    - Test: compare with UTC; low priority.

15. **What are the misc positive DIDs (`0x0600`, `0x6156`, `0x6167`,
    `0x6572`, `0xD476`, `0xE323`, `0xE445`, `0xE4C5`, `0xE535`)?**
    - Current evidence: positive response only.
    - Blocked by: no payload/context.
    - Next: recover capture; classify static vs live.

16. **Do recirculation `0x263B`, PTC `0x0801`, and seat heating `0x3ADF/3AE0`
    have the expected layouts?**
    - Current evidence: names from spot2000/evDash; no formulas for PTC/seat.
    - Test: off/on states; record raw length first.

17. **Is the 12 V current sign positive on discharge or charge?**
    - Current evidence: 0.536–5.482 A at rest; not tied to a known state.
    - Test: accessory/charge states; compare with DCDC output.

18. **Does 12 V power correlate with DC-DC output power?**
    - Current evidence: arithmetic only.
    - Test: drive/charge states; compare both derived powers.

## Investigation questions

19. **Did the electric-drive warning correlate with any real signal?**
    - Current evidence: no DTC; warning is a manual symptom; eldrive data is
      currently invalid due to F1.
    - Next: fix float32 first, then trend the valid signals around the next
      event.

20. **Is the low-voltage/communication problem a battery, DC-DC, or load
    issue?**
    - Current evidence: 12 V voltage 14.52–14.981 V, current 0.536–5.482 A,
      SoC 68–98%; not enough to separate.
    - Next: 12 V/DC-DC derived power plus state tests.

21. **Why does the OBC never answer?**
    - Current evidence: 211 requests, 0 successes; module may be asleep,
      address/profile mismatch, or session state.
    - Outside this catalogue; requires separate read-only diagnosis.

22. **Is the warning really extinguished, or only not currently present?**
    - Current evidence: owner report 2026-10-19; no DTC.
    - Next: monitor; do not treat absence as a fix.

23. **Is there a direct dashboard lamp / tell-tale DID?** (re-swept 2026-10-21)
    - Current evidence: a fresh search of every available source — the Car
      Scanner MEB profile (305 signals), the spot2000 VW MEB PID list, and the
      ABRP JSON — finds **no** lamp / tell-tale / malfunction-indicator /
      warning-light / MIL signal. The earlier "absence of a lamp channel"
      finding is confirmed.
    - Conclusion: the lamp state cannot be read directly. It is inferred from
      the DTC status byte bit 7 (`warningIndicatorRequested`); the collector now
      emits `dtc-cleared` when a code stops being reported, and
      `analysis/timeline.py` recovers `warning-on` / `warning-off` edges from
      the stored status-byte history.
    - Remaining: if a direct lamp channel exists at all it would sit on the
      instrument cluster (`inf`, `0x773 -> 0x7DD`), which the profile does not
      cover. A future read-only cluster sweep could settle it; do not live-probe
      it until the DTC-bit path has been exercised on a real event.

## Rules for closing a question

- A profile row does not close it.
- A plausible value does not close it.
- A single observation does not close a sign/scale question.
- A negative response must be recorded, not hidden.
- A decoder change requires a regression fixture and a review.
