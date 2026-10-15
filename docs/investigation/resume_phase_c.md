# Resume Phase C

Artefacts are all present. Start Phase C now:

1. Read il_getvagcan.txt fully (UTF-16LE) - confirms GetVAGCanRequests sequence: `ATFCSH{0};ATFCSD300005;ATFCSM1;ATCRA{1};1003`. This matches "reported GetVAGCanRequests sequence contains ATFCSH, ATFCSD, ATFCSM1, ATCRA and 1003". 
2. Inspect CarScannerMaui.dll around reported RVAs using the available scripts (inspect_dll.py, disasm*.py) or analyze structure. Look up: LoadVAG_MEB (0x35D158), GetVAGCanRequests (0x2274dc), GetVAGCode (0x217f8c), GetVAGKWPRequests (0x2271d8), BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2 (0x356654), ShiftStream2.ShiftReadByte (0x35795c).
3. Trace resource loading - see how vagmeb.db, vag.micro.vag, vag.strings.vag are loaded. Use existing decoded vagmeb.json as reference.
4. Build call graph labeling verified/inferred/unresolved. Don't assume DIDs from resource names alone.

Phase A/B: complete (docs+tests), real hardware validation outstanding but documented. No runtime changes made.
