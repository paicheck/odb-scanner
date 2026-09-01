# VW ID.3 Local Diagnostic System

Locally hosted, **strictly read-only** diagnostic and analysis system for a
2021 Volkswagen ID.3 58 kWh (VIN `WVWZZZE1ZMP087053`).

OBD adapter -> Python collector -> SQLite -> Python statistics -> local LLM
(Ollama) -> web dashboard. The LLM never talks to the vehicle; it only
interprets evidence computed from your own data.

```text
VW ID.3 → OBD-II port → ELM327/OBDLink (USB) → Windows PC
   → diagnostic collector → SQLite → validation + statistics
   → Ollama (local LLM) → FastAPI dashboard / reports
```

## Safety — READ ONLY

This system performs **no writes to the vehicle**. Enforced structurally in
`diagnostic/uds.py`, the only module that builds UDS requests:

* Allowed: `0x10` (default session), `0x22` ReadDataByIdentifier,
  `0x19 0x02` ReadDTCInformation, `0x3E` TesterPresent, OBD-II modes 01/03/09.
* Blocked (raises `ReadOnlyViolationError`): `0x14` clear DTCs, `0x27`
  security access, `0x2E` write data, `0x31` routine control, `0x2F` actuator
  control, `0x34–0x37` flashing, `0x28/0x85` communication control, and all
  others. There is **no API to send arbitrary payloads**.
* Every transmitted request is written to the `tx_log` table with its purpose
  (safety manifest).
* The LLM has no path to the adapter whatsoever.

## What a cheap ELM327 CAN and CANNOT access on an ID.3

| Data | Standard OBD-II | UDS (this project) |
|---|---|---|
| VIN, model year | ✅ mode 09/01 | ✅ |
| Emissions readiness, legislated DTCs | ✅ modes 01/03 | ✅ |
| 12 V ("control module voltage") | ✅ PID 0x42 | ✅ |
| SOC, pack V/I, cell voltages, BMS temps | ❌ | ⚠️ via DIDs — scale factors from OVMS e-Up docs, **ID.3 applicability must be verified at first connect** |
| Charging data (AC/DC, mode, timers) | ❌ | ⚠️ via DIDs (same caveat) |
| Motor rpm/torque/inverter temps | ❌ | ❌ no verified public DIDs — **not faked** (see `decoders/motor.py`) |
| Full coding/adaptations | ❌ | deliberately not implemented |

Unverified scale factors are labelled `experimentally determined` in the
database and dashboard; raw bytes are always preserved so decoders can be
corrected later without data loss.

## Windows installation

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.yaml config.local.yaml   :: optional personal overrides
```

Install [Ollama](https://ollama.com) and pull a model, e.g.
`ollama pull llama3.1:8b`. Set `OLLAMA_MODEL` in `config.yaml`.

## Testing without a car

```bat
:: terminal 1
.venv\Scripts\python main.py simulate

:: terminal 2
.venv\Scripts\python main.py discover          :: verify ECU discovery
.venv\Scripts\python main.py collect --cycles 1  :: one collection pass
.venv\Scripts\python main.py analyze           :: statistics + anomaly scan
.venv\Scripts\python main.py report            :: Ollama diagnostic report
.venv\Scripts\python main.py serve             :: http://localhost:8000

:: or load 30 days of example data instead
.venv\Scripts\python main.py seed
```

## With the real car

1. Ignition on (or vehicle awake), adapter plugged into the OBD port.
2. Set `adapter.port` in `config.yaml` (COM port from Device Manager), or
   leave `auto` for autodetection.
3. `python main.py discover` — records which registered ECUs answer and with
   which DIDs; NRC 0x31 answers are recorded, not errors.
4. `python main.py collect` repeatedly / on a schedule (Task Scheduler) to
   build history. Phase-3 DIDs will be refined against your specific car.

## Unit tests

```bat
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests -v
```

## Commands

| Command | Purpose |
|---|---|
| `python main.py simulate` | Built-in ID.3 simulator (TCP ELM327) |
| `python main.py discover` | ECU/DID discovery (read-only probes) |
| `python main.py collect --cycles N` | Collection loop into SQLite (omit N for continuous) |
| `python main.py analyze` | Statistical analysis + anomaly scan, persisted |
| `python main.py serve` | Web dashboard at `localhost:8000` |
| `python main.py report` | Print AI report to console (`--question "..."`) |
| `python main.py seed` | Load example 30-day dataset |
| `python main.py guard-test` | Verify the read-only UDS guard |

## Key limitations

* Standard OBD-II on the ID.3 exposes only powertrain basics — cell voltages
  etc. require UDS DIDs (see table above).
* OVMS scale factors are for the e-Up/MEB family; treat ID.3 values as
  provisional until cross-checked (e.g. SOC vs displayed SOC, pack voltage
  vs sum of cells).
* Drive-system live data needs a CAN-capable adapter (python-can /
  OBDLink STN) — planned future phase; no DIDs are invented meanwhile.
* The LLM output is hypothesis-ranking with confidence levels, never a
  definitive diagnosis; the automated validator flags over-confident wording.
