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

### Network exposure — read this before connecting it to anything

The dashboard and the `/ai/ask` endpoint have **no authentication and no CSRF
protection**. Anyone who can reach the port can read the vehicle history and
submit prompts to the LLM. This is deliberate for the intended use — a phone or
tablet on a private Wi-Fi network talking to a laptop running the collector — but
it is a real boundary, not an oversight:

* Do not port-forward or expose the port to the internet.
* On shared or untrusted Wi-Fi, bind to localhost only and reach it over an SSH
  tunnel, or put a reverse proxy with authentication in front of it.
* The read-only guarantee described above is about the **vehicle**: no request
  the dashboard can trigger writes to the car. It says nothing about who can
  reach the dashboard itself.
* HTML is rendered through Jinja2 with autoescaping on, and no template opts out
  via `|safe`, so stored vehicle values are not an injection vector.

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
copy config.local.yaml.example config.local.yaml   :: optional, gitignored
```

`config.yaml` holds the shareable defaults and is meant to be committed.
`config.local.yaml` is optional and gitignored: anything in it is deep-merged
over `config.yaml`, so you only need to list the keys you want to change.

```yaml
# config.local.yaml -- personal overrides only
adapter:
  port: COM7          # nested sections merge, so adapter.baudrate is kept
ollama:
  model: llama3.1:8b
```

The same three Ollama keys can be set as the environment variables
`OLLAMA_HOST`, `OLLAMA_MODEL` and `OLLAMA_TIMEOUT`, which win over both files.

Install [Ollama](https://ollama.com) and pull a model, e.g.
`ollama pull llama3.1:8b`.

## Testing without a car

One command proves the whole pipeline works (simulator → collector → SQLite →
statistics → web → read-only guard), no car and no Ollama required:

```bat
.venv\Scripts\python tools\smoke_test.py
.venv\Scripts\python tools\smoke_test.py --cycles 5   :: more samples
.venv\Scripts\python tools\smoke_test.py --with-llm   :: also test Ollama
```

It prints one `[PASS]/[FAIL]/[SKIP]` line per check, uses the scratch database
`data/diag_smoke.db` (safe to delete), and exits non-zero if anything fails.

Manual equivalent, in two terminals:

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

## Running the stack continuously

### If you would rather not type commands

On Windows there are double-clickable shortcuts next to this file, and
`HOW_IT_WORKS.txt` explains them in plain English. Start with
**`5_TRY_IT_NOW.bat`** (needs no car) then **`1_START_COLLECTING.bat`**.

| Shortcut | Does |
|---|---|
| `1_START_COLLECTING.bat` | check the link, then start collecting + dashboard |
| `2_DOCTOR.bat` | work out why it will not connect |
| `2_DOCTOR_ALL_PORTS.bat` | try every COM port (Windows Bluetooth creates two) |
| `3_STOP.bat` | stop the collector and dashboard |
| `4_STATUS.bat` | what is running right now |
| `5_TRY_IT_NOW.bat` | test the whole pipeline with no car |

They only wrap the commands below, so anything you can do here you can do
by double-clicking instead.

### Doing it by hand

For a live dashboard instead of a one-shot test, `tools/start_scanner.py`
starts the simulator, collector and dashboard detached, writes logs to
`data/logs/{simulator,collector,web}.log` and remembers the pids in
`data/logs/stack.json`. Re-running it is safe: whatever is already up is
reported and left alone, so nothing is started twice.

```bat
.venv\Scripts\python tools\start_scanner.py            :: simulator + collector + dashboard
.venv\Scripts\python tools\start_scanner.py --cycles 1  :: single collection pass, then stop collecting
.venv\Scripts\python tools\start_scanner.py --no-web    :: adapter + collector only
.venv\Scripts\python tools\start_scanner.py --status    :: what is running right now
.venv\Scripts\python tools\start_scanner.py --stop      :: stop everything it started

start http://127.0.0.1:8000                             :: dashboard
```

The adapter link stays open between polls (`collector.poll_interval`), just like
a real ELM327 session, so the simulator must not treat an idle read timeout as a
disconnect — `tools/smoke_test.py` guards that behaviour with an explicit idle
gap check.

## With the real car

1. Ignition on (or vehicle awake), adapter plugged into the OBD port.
2. Set `adapter.port` in `config.yaml` (COM port from Device Manager), or
   leave `auto` for autodetection.
3. `python main.py discover` — records which registered ECUs answer and with
   which DIDs; NRC 0x31 answers are recorded, not errors.
4. `python main.py collect` repeatedly / on a schedule (Task Scheduler) to
   build history. Phase-3 DIDs will be refined against your specific car.
5. If commands answer `NO DATA`, the vehicle is asleep: the adapter powers
   itself from the OBD port, but the gateway only answers with ignition on /
   'ready' mode. Retry after switching ignition on. `python tools/elm_console.py`
   opens a raw ELM327 console — send `0100` to check whether anything answers.

## Unit tests

```bat
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests -v
```

107 tests, no car and no Ollama required. A few drive a fake adapter over a
real socket and are marked `slow`, so the quick inner loop is:

```bat
.venv\Scripts\python -m pytest -m "not slow"   :: 98 tests, ~2s
.venv\Scripts\python -m pytest -m slow         :: the 9 that open sockets
```

### Linting

```bat
.venv\Scripts\pip install ruff
.venv\Scripts\python -m ruff check .
```

Config lives in `pyproject.toml`. Two rules are switched off on purpose:
`E402`, because imports sit next to the section they support, and `B905`,
because the `zip()` calls here pair sequences the caller has already
length-checked.

### Continuous integration

`.github/workflows/ci.yml` runs lint, the fast tests, the full suite, the
offline smoke test and `guard-test` on Python 3.11–3.14. The smoke test and
the read-only guard are separate steps on purpose: they catch wiring breaks
and safety regressions that the unit tests would not.

### Dependency pinning

`requirements.txt` holds the accepted version ranges. `requirements-lock.txt`
pins the exact versions verified working (the 107 tests and the smoke test
above) if you want a reproducible environment instead of the newest allowed.
Only direct dependencies are pinned, so the same file is valid on Windows and
on Linux/macOS, where the compiled wheels differ.

## Commands

| Command | Purpose |
|---|---|
| `python main.py simulate` | Built-in ID.3 simulator (TCP ELM327) |
| `python main.py discover` | ECU/DID discovery (read-only probes) |
| `python main.py collect --cycles N` | Collection loop into SQLite (omit N for continuous) |
| `python main.py analyze` | Statistical analysis + anomaly scan, persisted |
| `python main.py serve` | Web dashboard at `localhost:8000` |
| `python main.py report` | Print AI report to console (`--question "..."`) |
| `python tools/smoke_test.py` | Offline end-to-end self-test (no car, no Ollama) |
| `python tools/elm_console.py` | Raw ELM327 console for live debugging (`--tcp` for simulator) |
| `python tools/start_scanner.py` | Start/stop the simulated stack (`--status`, `--stop`) |
| `python main.py seed` | Load example 30-day dataset |
| `python main.py guard-test` | Verify the read-only UDS guard |
| `python main.py doctor` | **Work out why the adapter is not connecting** |
| `python main.py prune --days 90` | Drop stale raw response bytes, keeping every parsed value |
| `python main.py prune --days 90 --hard` | Delete history rows older than 90 days entirely |

### Database growth

An always-on collector appends a few hundred rows a minute, so an unattended
database grows without bound. `prune` never runs automatically — deleting data
is your call. The default drops only the verbatim vehicle bytes older than
`--days`, keeping every parsed value, so trends and reports are unaffected.
`--hard` also deletes the rows, and is the only way to reclaim the space
promptly (it vacuums). Vehicles, ECUs, sessions, DTCs and generated reports are
never pruned.

## When it will not connect — run the doctor

`NO DATA` from every request looks identical whether the cause is a wrong COM
port, an unpaired Bluetooth link, a locked port, a pinned protocol or a sleeping
car. `python main.py doctor` works out which layer stopped answering:

```
[1/8] Configuration ......... OK      adapter.port COM3
[3/8] Port open ............. OK      COM3 opened
[4/8] Adapter identity ...... OK      ATI  Veepeak BLE+ v3.1
[5/8] Protocol negotiation .. FAIL    ATSP6 CAN 11-bit 500k  no answer
                                         ATSP7 CAN 29-bit 500k  no answer
                                         ...
[6/8] OBD-II bus ............ FAIL    0100 -> NO DATA
Verdict
  1. The adapter works, but the car never answered. This is a vehicle-side or
     power problem, not a tool problem.
  1. Turn the ignition ON and leave it on.
  2. Reseat the OBD plug...
```

It is read-only: every request that could reach the vehicle goes through
`uds.validate_request`, so the tool cannot transmit a write even if edited.

```
python main.py doctor                      # configured adapter
python main.py doctor --port COM7          # try another port
python main.py doctor --all-ports          # probe every port until one opens
python main.py doctor --only-config        # check settings without hardware
python main.py doctor --tcp 127.0.0.1 35000  # simulator / TCP bridge
```

The three findings worth knowing about, because they are not guessable:

* **Bluetooth SPP on Windows creates two COM ports** — an outgoing one that
  carries your bytes and an incoming one that does not. The incoming port opens
  cleanly and returns nothing at all, so it looks like a dead adapter. Stage 2
  lists every port with its description and flags the one you configured.
* **A refused `ATSP` is not a sleeping car.** If the adapter answers `?` to a
  protocol change, it is pinned to whatever protocol it powered up with, and no
  amount of waiting will help — unplug it fully to reset. Stage 5 records
  refusals separately from silence precisely because the two otherwise look
  identical.
* **Only one process may hold the port.** The dashboard, `main.py collect` and
  `tools/elm_console.py` all fail to open it while another holds it, and on
  Bluetooth SPP there is no sharing at all.

For raw bytes, `python tools/elm_console.py --no-init` shows exactly what is
arriving when the doctor's verdict is not enough.

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
* A per-day slope needs per-day data. The cell-delta trend reports
  `insufficient_span` until the samples cover at least 1 day, because
  extrapolating a slope in mV/day from a few seconds of polling yields
  meaningless numbers. Override the 1-day floor per call via the
  `min_span_days` argument to `analysis.battery.cell_delta_trend()`.
* LLM reports are not persisted while the database has no vehicle row
  (`llm_reports.vehicle_id` is `NOT NULL`); the report is still returned and a
  `NOT PERSISTED` warning is attached.
