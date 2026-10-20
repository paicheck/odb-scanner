# Research backlog

Status: active. Additive research items only. No live vehicle work is approved
in this phase.

| ID | Item | Priority | Status | Next action | Evidence needed | Blocked by |
|---|---|---|---|---|---|---|
| B01 | Float32 decoder family (25 eldrive rows) | P1 | Evidence complete; change not applied | Add offline regression vectors, then a reviewed float32 decoder | Raw bytes + profile formula; already available | Code review/approval |
| B02 | Cell/sensor extreme indices | P1 | Evidence complete; change not applied | Add 1-based outputs; tie-aware UI | 31-timestamp cross-check; already available | Code review/approval |
| B03 | 12 V / DC-DC derived power | P1 | Definition complete | Add derived metrics; validate sign convention | Arithmetic; state test later | Schema/UI decision |
| B04 | `0x1E32` four-field block | P2 | Evidence complete; change not applied | Add Ah/kWh fields and short-block test | Raw 16-byte block; already available | Code review/approval |
| B05 | Pack-current sign documentation | P1 | Evidence complete | Fix docstring/label; do not invert | `0x1E3D`, `0x1E3E`, `0x035E`; already available | Documentation/approval |
| B06 | CO2 `0x42DB` field/width | P4 | Open | Fresh-air/occupant test; compare byte1/u16 | Known-state observations | Vehicle approval |
| B07 | Windshield `0x27C4` field/scale | P4 | Open | Cold-soak/defroster test; log five bytes | Known-state observations | Vehicle approval |
| B08 | `0x5867` odometer scale | P3 | Open | Log four odometers on one drive | Known-distance drive | Vehicle approval |
| B09 | ABS wheel speeds `0x1800`–`0x1803` | P3 | Profile/capture evidence only | Separate `0x10 03` approval; all four wheels at crawl | Raw values from ABS | Explicit high-risk approval |
| B10 | Gateway `0x2A26/0x2A28/0x2A2F` payloads | P3 | Blocked | Locate original btsnoop or a saved capture; extract request/response/length | Raw payloads | Missing capture |
| B11 | Misc positive DIDs (`0x0600`, `0x6156`, `0x6167`, `0x6572`, `0xD476`, `0xE323`, `0xE445`, `0xE4C5`, `0xE535`, `0x0313`, `0x305A`) | P4 | Backlog | Recover context/payloads; classify static vs measurement | Capture context | Missing capture |
| B12 | GPS `0x2430` / `0x22B3` | P3 | Source-supported | Implement multiframe parse; one outdoor read | Raw fix/time | Multiframe support + approval |
| B13 | HVAC recirculation `0x263B` | P3 | Source-supported | Read fresh vs recirc; confirm 00/04 | Raw state | Vehicle approval |
| B14 | PTC air heater `0x0801` | P3 | Speculative | PTC-off/on test; raw length/scaling | Raw values | Vehicle approval |
| B15 | Seat heating `0x3ADF`/`0x3AE0` | P4 | Speculative | Off/on test; likely comfort only | Raw values | Vehicle approval |
| B16 | Battery serial `0x0500` decode | P4 | Observed, not decoded | Decode ASCII; store text | Raw ASCII; already available | Low priority |
| B17 | BMS `0xF802` cleanup | P2 | Rejected | Mark unsupported/remove from slow loop | 63 attempts, NRC `0x31`; already available | Code cleanup |
| B18 | `bat_charging_voltage` sentinel | P4 | Open | Investigate 0/`0xFFFF` sentinel; do not use as voltage | Raw states | Low priority |
| B19 | `dc_dc_converter_reserved_current` meaning | P4 | Open | Identify field; 243–252 A is impossible as current | Raw/context | Low priority |
| B20 | OBC no-response root cause | P3 | Outside this catalogue | Review 211 failures, session state, module wake-up | Live read-only diagnostics | Separate approval |

## Backlog rules

- A profile definition does not close an item; raw evidence or a controlled
  test does.
- A positive `0x62` response closes only the “does it answer?” question, not
  the “what does it mean?” question.
- Do not add a decoder for an unknown payload just to make the dashboard fuller.
- Do not batch multiple unknowns into one live session; one variable at a time.
- Re-check this backlog after any vehicle test or decoder change.
