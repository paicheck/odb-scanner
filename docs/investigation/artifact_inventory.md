# Artefact Inventory

## Found in C:\Users\kings\AppData\Local\Temp\opencode

### Reverse-engineering scripts
- extract_store.py (3.7 KB, 2026-10-13 22:40:01)
- disasm.py (2.1 KB), disasm2.py (3.7 KB), disasm3.py (2.9 KB)
- inspect_dll.py (1.1 KB)
- find_meas.py (401 B, 2026-10-13 22:54:12)
- extract_one.py (735 B, 2026-10-13 22:46:08)
- decrypt.py (431 B, 2026-10-13 22:52:11)
- bson2json.py (634 B, 2026-10-13 22:52:29)
- profile_csv.py (2.2 KB), reported_rows.py (1.3 KB)

### Managed assemblies
- carscanner_dlls/CarScannerMaui.dll (39.9 MB, 2026-10-13 22:40:17) SHA256: 014519a11d3dfbfb...
- carscanner_dlls/CarScannerMaui.Droid.dll (231 KB)
- Supporting .dlls (complete set)

### Resources/decoded
- vagmeb.db (26.4 KB) sha256:a430aeafd6f83b31
- vagmeb.dec.bin (26.4 KB) sha256:6875c0cc0fb2e84b
- vagmeb.json (35.9 KB) sha256:51ffdb4db65feac4
- res_micro_vag (6.2 MB) sha256:39b19d024851162e (vag.micro.vag)
- res_strings_vag (3.1 MB) sha256:c2ae1d758237a2f0 (vag.strings.vag)
- carscanner_strings.txt (15.6 MB) sha256:7ef3fb469e640b38

### Analysis
- il_getvagcan.txt (35.1 KB) sha256:972bf1847b56c281 (contains GetVAGCanRequests, RVA 0x2274dc; UTF-16LE)
- spot2000.csv (40.7 KB)

## Duplicate note
Car Scanner dir in Documents has native .so files but not managed DLLs/resources needed - working set is in opencode temp. No duplicate managed assemblies found.

## Status
All required artefacts present. Ready to resume Phase C.
