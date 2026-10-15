# Transport baseline (Phase B)

> **Addendum 2026-10-15 — gateway requests.** `ECUS["gate"]` was added
> (tx `0x792`, rx `0x710`) for the `[19.Gate]` energy-content DIDs, so this
> file's transport model now covers a second ECU class. Three points that the
> Car Scanner MEB profile clarified:
>
> 1. **What ATCRA means there is still unresolved.** In that profile
>    `ATCRA = response header + 0x6A` for all five 11-bit ECU classes
>    (`0x710`→`0x77A`, `0x746`→`0x7B0`, `0x744`→`0x7AE`, `0x713`→`0x77D`,
>    `0x767`→`0x7D1`). So the Car Scanner `ATCRA77A` argument is **not** a
>    response CAN id, and we deliberately filter on the declared response
>    header `0x710` instead of copying it. See `meb_gateway_energy.md` §4.
> 2. **ATFCSH / ATFCSM / ATFCSD are still not implemented.** They appear in
>    the reference `BCM` (`ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A`),
>    but they are ELM *flow-control* settings; this transport issues only
>    `ATSH`/`ATCRA` and relies on the ELM for flow control. Adding them would
>    change live behaviour on an unvalidated assumption.
> 3. **No ATSH is implied by the gateway records.** They carry no request
>    header at all, so the `0x792` request id remains a code-name inference
>    (`0x772` = `0x710` − 8 is the untested alternative).

## Initialization sequence
From elm327.py:
- ATZ -> wait 0.3s, ATI, then AT E0, ATL0, ATS0, ATH1, ATAT1, ATSP6, ATCAF1
- ATH1: headers ON (required for addressing specific ECUs via UDS). This affects parsing: responses include CAN headers (e.g. 7E8 ...).
- ATSP6: ISO 15765-4 CAN 500kbit/s 11-bit (default). No extended CAN configured.
- Timeout default 5.0s; read until '>'.

## Set addressing
- set_header(tx_id): `ATSH{tx&0x7FF:03X}` (11-bit). No handling for 29-bit/extended addresses (0x18DA, 17FC etc). 
- set_receive_address(rx_id): `ATCRA{rx:03X}` or `ATCRA` to clear filter.

## Parsing
- uds.parse_elm_lines collects frames from lines; line_to_frame accepts header-on (`7ED04621...`) or header-off (`04621...`), strips non-hex, tries both candidates, validates frame PCI.
- Reassembly: single-frame PCI 0; multi-frame PCI 1 (first) + PCI 2 (consec) with 12-bit total length and sequence. Returns payload bytes.

## BMS regression example (working)
- Request `22 1E3B` (DID 0x1E3B) to ECU bat_mgmt (tx 0x7E5, rx 0x7ED) with headers ON. Simulator returns `7ED 10 05 62 1E 3B VV VV` or encoded as frames; collector passes to connection.read_did -> returns data after DID (3 bytes after 0x62 1E3B? positive is 0x62 + DID + data; read_did takes positive[3:]). 

## Error classification (proposed, matches current behavior)
Current:
- AdapterNotFoundError (open fails), CommunicationError (write/read/timeout/no prompt), ProtocolError (empty/malformed/unexpected response), NegativeResponseError (NRC). Also test recognizes "NO DATA"/"SEARCHING..." as no-frame -> parse returns None (CommunicationError in read_did). 

Classifications to distinguish:
- Adapter rejection: command gives `?` or `ERROR` in response (ELM indicates bad command). Current parser doesn't explicitly classify `?`/`ERROR` as adapter rejection vs data; lines with no valid frame become None.
- Timeout: CommunicationError "Timeout waiting for prompt".
- No data: parse_elm_lines returns None -> "No/undecodable response..." (could be ECU silent or unsupported). 
- Malformed response: ProtocolError on DID echo mismatch, unexpected response.
- Negative UDS response: NegativeResponseError (NRC). 
- Positive UDS response: success path.
- Incomplete/multi-frame: reassembly returns None if frames incomplete/out of sequence? reassemble stops when data>=total, but if sequence broken or missing, returns bytes(data[:total]) only if we broke on condition; if we never got enough and frames end, we return truncated bytes (data[:total]). This could be "incomplete".
- Flow-control failure: not observed in current path (ELM327 handles ISO-TP FC for requests it sends). 

## `?` responses / "energy" vs "climate" (not present)
Task reports attempts to access `energy` and `climate` modules return ELM327 `?`. Those module names don't exist in this codebase. The baseline uses direct UDS DIDs (0x22) to registered DIDs; no code references "energy" or "climate". So "failing energy/climate requests" must relate to the external Car Scanner context (not this repo). For this repo, all implemented DIDs read via UDS 0x22 on appropriate ECUs with 11-bit ATSP6 + headers ON. 

## Apparent inconsistency: ATSP6 (11-bit) vs extended headers 17FC/17FE/18DA
- Current code sets ATSP6 (11-bit) and sends 11-bit tx IDs (0x7E5 etc). It does not send/expect 29-bit extended CAN IDs. The reported extended headers (17FC007B, 17FE007B, 18DA...) are 29-bit identifiers common in some VAG UDS-on-CAN implementations (e.g. VW TP20/UDS with specific functional/physical addresses); ELM327 may need ATSP7 (ISO15765-4 29-bit) or header formatting differs. Also ATH1 + ATSH with 29-bit values behaves differently depending on adapter firmware. This repo uses 11-bit scheme throughout; we must treat 17FC/17FE as potentially gateway-mediated UDS (different addressing) that would require different ATSP/ATSH config - need to verify from Car Scanner artifacts.
- No code here uses extended addresses. So changing this is a big experimental step; must not guess. Need evidence from disassembly.

## Conclusion
Transport model is straightforward 11-bit UDS with headers ON. The "energy/climate" failures aren't present in this codebase. To proceed per task, we need the Car Scanner APK/extracted assemblies/resources to find the actual live-data request sequences (including addressing) used for [19.Gate] hv_energy and [8105.DC/DC] etc. The next step is Phase C: trace Car Scanner live-data code path - locate LoadVAG_MEB, GetVAGCanRequests etc in extracted artifacts.

**Update 2026-10-15:** the Car Scanner artefacts were obtained and the
`[19.Gate]` hv_energy request sequence *was* recovered — UDS `0x22` with DID
`0x2AB8`/`0x2AB2`, response header `0x710`, plain ISO-TP (no VWTP). See
`meb_gateway_energy.md`. What remains is the in-car confirmation of the
addressing pair and the `0x2AB8` payload offset, not further tracing.

