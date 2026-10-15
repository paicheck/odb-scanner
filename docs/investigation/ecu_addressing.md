# ECU addressing investigation (Phase E)

> **Update 2026-10-15.** A `gate` entry (tx `0x792`, rx `0x710`) now exists in
> `diagnostic/ecus.py`, from the recovered Car Scanner MEB profile
> (`[19.Gate]` records). The `0x79A` guess below is **wrong** and has been
> dropped: in that profile `ATCRA = response header + 0x6A` for all five
> 11-bit ECU classes, so `ATCRA77A` is not a raw response CAN id. See
> **`meb_gateway_energy.md`** §4 for the full reasoning and the fallback
> order. Nothing here has been validated on the vehicle.

## Current codebase (verified)
- Transport: ATSP6 (ISO 15765-4 CAN 500kbit/s 11-bit), ATH1 headers ON. All tx/rx are 11-bit (0x7E5/0x7ED, etc.)
- `set_header(tx_id)` sends `ATSH{tx&0x7FF:03X}`, `set_receive_address(rx)` sends `ATCRA{rx:03X}` (or clear)
- BMS works via 11-bit UDS (0x7E5 request / 0x7ED response). This matches documented MEB VAG UDS-on-CAN for some ECUs.

## Car Scanner verified Gateway addressing (from audit report)

| Identifier | Direction | Provenance | Verified as Gateway? |
|------------|-----------|------------|----------------------|
| `0x792` | 11-bit (req) | `BuildList` @ 0x1ffc60, `GetECUName` @ 0x188dd0 | **YES** — explicitly named "Gateway" in executable code |
| `0x710` / `0x77A` | 11-bit (req/resp) | `GetVAGCanRequests` scan list | In scan list, but **no label** attached |
| `18DAD0F1` | 29-bit normalized | String heap "GATEWAY (29 bit)" | **Name only** — no consumer code binds it to Gateway ECU |
| `17FC0076` / `17FE0076` | 29-bit VAG | BuildList for "19. CAN Gateway" | **Misleading label** — this is Vehicle Info module (code `0076`) |

**Conclusion**: The only code-verified Gateway address is **11-bit `0x792`** (request). ~~Response is likely `0x79A` (req+8) following VAG convention~~ — **superseded 2026-10-15**: the recovered `[19.Gate]` records declare response header `0x710`, and their `ATCRA` argument `0x77A` is `0x710 + 0x6A`, i.e. not a response CAN id. See `meb_gateway_energy.md` §4.

## Extended addressing (unverified here)
- 17FC/17FE are 29-bit CAN IDs (extended). To use them with ELM327, typically need ATSP7 (29-bit) and/or different header formatting; behavior varies by adapter.
- 18DAxxxx is ISO 15765-2 functional/physical addressing in some implementations (VAG UDS often uses specific physical function addresses).

## Analysis
- 11-bit vs 29-bit: Current implementation hardcodes 11-bit assumptions throughout (masks to 0x7FF, uses ATSP6).
- Gateway [19.Gate]: The Car Scanner audit report confirms 11-bit `0x792` is the only code-verified Gateway address. This is a 11-bit identifier, consistent with the existing transport.

## Required evidence (still missing)
- Confirmation that the gateway answers at `ATSH792`/`rx 0x710` on the ID.3 (requires the authorised read-only in-car test)
- Whether hv_energy_content/max DIDs are served by the gateway at this address

## Conclusion
The gateway is now registered as `gate` (tx `0x792`, rx `0x710`) on the
strength of the recovered `[19.Gate]` measurement records, which is a static
third-party definition, not an observation of this vehicle. It is expected to
report `no-response` or an NRC until the in-car validation in
`meb_gateway_energy.md` §7 is performed. The 11-bit `0x792` address itself
is unchanged from the earlier audit and remains the only gateway address named
in Car Scanner code.

