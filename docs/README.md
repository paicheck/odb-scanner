# docs/

Two kinds of document live here, and it matters which one you are reading.

## Living documents — kept current

| Document | What it is for |
|---|---|
| [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md) | Start here: what the project is, the safety model, current state, open questions |
| [`../README.md`](../README.md) | Setup, commands, the read-only guarantee and network exposure |
| [`../CAR_CHECK.md`](../CAR_CHECK.md) | What to do at the car, step by step |
| [`DATA_CATALOGUE.md`](DATA_CATALOGUE.md) | Every measurement key, its source and how far it is verified |
| [`MEB_DIAGNOSTIC_REFERENCE.md`](MEB_DIAGNOSTIC_REFERENCE.md) | MEB addressing, modules and DIDs as currently understood |
| [`ADAPTERS.md`](ADAPTERS.md) | Which OBD interfaces the app can use, how it finds and selects them, and their limits |
| [`OBSERVATIONS_DB.md`](OBSERVATIONS_DB.md) | The normalised observations database built from collected data |
| [`REGRESSION_GUARDS.md`](REGRESSION_GUARDS.md) | Why the MEB read-path tests assert what the adapter receives, and which bugs they pin |
| [`../cloud/DEPLOY.md`](../cloud/DEPLOY.md), [`../cloud/CPANEL.md`](../cloud/CPANEL.md) | Deploying the cloud ingest API (VPS / cPanel) |

`experiment_record_results.md` and `experiment_record_sim.md` are written by
`tools/diagnose_meb_path.py`; regenerate rather than edit them.

## Evidence and history — dated, not updated

[`investigation/`](investigation/) holds the dated audits, reverse-engineering
notes, replay reports and plans. They record what was known **on their date**
and several are explicitly superseded; the living documents above say what
holds now. Do not move or rename files there: `decoders/catalogue.py` and the
exported Android spec cite them by path as the evidence behind each decoder.

[`CAR_SCANNER_REVERSE_ENGINEERING.md`](CAR_SCANNER_REVERSE_ENGINEERING.md) and
[`MEB_NO_RESPONSE_HANDOFF.md`](MEB_NO_RESPONSE_HANDOFF.md) belong to the same
evidence set but stay at this level because code and many documents link to
them here.
