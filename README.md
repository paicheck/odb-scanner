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

**One deliberate exception: `tools/elm_console.py --raw-unsafe`.** The
console is read-only by default: `AT`/`ST` adapter commands are sent as typed,
and every vehicle request must pass the same `validate_request` allow-list, so
a stray `2E`, `31`, `1002` or `14` is refused. Started with `--raw-unsafe` it
sends whatever you type, with no allow-list — that mode exists for discovery
work, says so on every prompt, and is the one place the guarantee above does
not extend to. Everything else — `collect`, `discover`, `doctor`,
`guard-test`, the dashboard — goes through the allow-list.

### Network exposure — read this before connecting it to anything

`config.yaml` binds the dashboard to `0.0.0.0` so a tablet on the same Wi-Fi
can open it. Because that bind is reachable from the network, the dashboard
**requires an access token** (`web/auth.py`):

* On the first run a token is generated, written to `config.local.yaml`
  (gitignored) and printed at startup, along with the URL to open.
* Opening `http://<this-machine>:8000/?k=<token>` once exchanges it for an
  `HttpOnly` cookie, so the token stops appearing in the address bar — and
  therefore in browser history, bookmarks, screenshots and `Referer` headers.
  Every later visit works normally.
* Bind to `127.0.0.1` instead and no token is needed at all: nothing but that
  machine can open a loopback socket, so there is nobody to authenticate.

Set `web.auth_token` in `config.local.yaml` (or export `ODB_TOKEN`) to choose
the value yourself. The token is sent in the clear over plain HTTP — which is
the assumption for a home network. Further boundaries:

* **Cross-site requests:** `SameOriginMiddleware` (`web/auth.py`) refuses any
  POST whose `Origin` (or, failing that, `Referer`) is not the dashboard's own,
  whether or not a token is configured — so a page you visit cannot make your
  browser submit `/ai/ask` or create investigations. A request carrying
  neither header (curl, a script) is not browser-driven and is let through;
  on a network bind it still needs the token.

* Do not port-forward or expose the port to the internet.
* On shared or untrusted Wi-Fi, bind to `127.0.0.1` and reach it over an SSH
  tunnel, or put a reverse proxy with TLS in front of it.
* `POST /ai/ask` is rate-limited only by an input length cap. Anything that can
  authenticate can spend minutes of GPU time per request.
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
| HV battery energy content / maximum | ❌ | ✅ DIDs `0x2AB8` / `0x2AB2` on the gateway (0x710 → 0x77A) — addressing, offsets and multipliers confirmed by a passive HCI capture on 2026-10-15 (`docs/investigation/meb_gateway_energy_passive_capture_20261015.md`) |
| Motor rpm/torque/inverter temps | ❌ | ⚠️ DIDs from the Car Scanner MEB profile (electric-drive module 0x7C, 29-bit) — **experimental**, not yet confirmed on this car |
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

### Simulated and seeded data never share the car's database

The simulator answers with plausible engineering values and the collector stores
them by exactly the same code path as a real reading, so the *file* a run writes
to is the only thing keeping demo data out of the vehicle's record. (It is not
hypothetical: 265,014 simulator readings and a month of seeded history once
ended up under this car's own VIN, with nothing on any row to say so.)

| run | database | VIN reported |
| --- | --- | --- |
| real car (`adapter.type: elm327_serial`) | `database.path` | `vehicle.vin` |
| simulator + collector | `simulation.db_path` | `simulation.vin` |
| `python main.py seed` | `seed.db_path` | `seed.vin` |

Three independent defences, so one mistake is not enough to mix them:

* **Separate files.** `tools/start_scanner.py` points the collector it starts in
  simulator mode at `simulation.db_path` via `ODB_DB_PATH`. Config loading
  refuses a config where `simulation.db_path` or `seed.db_path` equals
  `database.path`.
* **Separate identity.** The simulator reports `simulation.vin`, not the car's,
  so a run that lands in the wrong file still lands on a different vehicle row.
* **Separate label.** The collector recognises the simulator's adapter
  identification string and writes `provenance='simulated'` on every measurement
  it records. Those rows show as *simulated* on `/sources`, are excluded from the
  live/stale counts, and the cloud uploader never sends them.

If a database already holds both, `tools/purge_synthetic_data.py` reports what
it would remove and deletes it with a snapshot of its own:

```bat
.venv\Scripts\python tools\purge_synthetic_data.py            :: dry run
.venv\Scripts\python tools\purge_synthetic_data.py --apply    :: back up, then delete
```

It removes every reading older than the first real adapter connection
(`--before` to change the cutoff), keeps imported Car Scanner evidence and the
adapter-connection log that justifies the decision, and drops anything already
tagged `simulated` whatever its date.

## Running the stack continuously

### If the adapter disappears

A Bluetooth SPP link drops — the phone sleeps, walks out of range, or the
ignition goes off — and the collector tells the difference between *the adapter
is gone* and *the vehicle is merely asleep*. A car that is off simply refuses
its DIDs; an adapter that has vanished fails every read, including the OBD-II
handshake that needs no ECU cooperation. When a cycle fails entirely, the
collector stops cycling, records the outage as an event, and reconnects on a
backoff from 5 s to 5 minutes rather than spinning. A successful reconnect
forces a full slow pass, because the vehicle may have driven or charged while
the collector was blind.

Previously the two cases looked identical: `collect_once()` returned an empty
snapshot, the loop kept running at `poll_interval` forever, and each cycle
wrote a failed row for every registered DID — 146 rows per 3 cycles, with
nothing in the log to say why.

### If you would rather not type commands

On Windows there are double-clickable shortcuts next to this file, and
`HOW_IT_WORKS.txt` explains them in plain English. Start with
**`5_TRY_IT_NOW.bat`** (needs no car) then **`1_START_COLLECTING.bat`**.
**`0_OPEN_GUI.bat`** opens a desktop window with all of the actions as buttons.

| Shortcut | Does |
|---|---|
| `0_OPEN_GUI.bat` | open the desktop GUI (all the actions as buttons) |
| `1_START_COLLECTING.bat` | check the link, then start collecting + dashboard |
| `2_DOCTOR.bat` | work out why it will not connect |
| `2_DOCTOR_ALL_PORTS.bat` | try every COM port (Windows Bluetooth creates two) |
| `3_STOP.bat` | stop the collector and dashboard |
| `4_STATUS.bat` | what is running right now |
| `5_TRY_IT_NOW.bat` | test the whole pipeline with no car |

The command shortcuts only wrap the commands below, so anything you can do
here you can do by double-clicking instead.

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

728 tests, no car and no Ollama required. A few drive a fake adapter over a
real socket and are marked `slow`, so the quick inner loop is:

```bat
.venv\Scripts\python -m pytest -m "not slow"   :: 714 tests
.venv\Scripts\python -m pytest -m slow         :: the 14 that open sockets
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
pins the exact versions verified working (the 728 tests and the smoke test
above) if you want a reproducible environment instead of the newest allowed.
Only direct dependencies are pinned, so the same file is valid on Windows and
on Linux/macOS, where the compiled wheels differ.

### Versioning

The application version is one string, stored once in `version.json` at the
repo root and shared by both apps, so they cannot drift:

* Python CLI: `python main.py --version` (and `tools/doctor.py --version`);
  importable as `version.__version__`.
* Android app: `android/app/build.gradle.kts` reads the same file for
  `versionName`, and derives `versionCode` from it
  (`MAJOR*10000 + MINOR*100 + PATCH`), so a release bump is a single edit.

Two other version strings exist and are deliberately separate: `spec.json`'s
`spec_version` (the data-schema / read-only allow-list) and
`decoders/catalogue.py`'s `CATALOGUE_VERSION`. `tests/test_versioning.py` pins
the scheme.

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
| `python tools/collect_acceptance.py` | One bounded live run, then check the database holds pack voltage / SoC / cells (`--sim` for an offline self-test) |
| `python tools/elm_console.py` | ELM327 console for live debugging, read-only allow-list (`--tcp` for simulator, `--raw-unsafe` for no allow-list) |
| `python tools/start_scanner.py` | Start/stop the simulated stack (`--status`, `--stop`) |
| `python tools/import_carscanner.py "export.csv"` | Import a Car Scanner CSV as evidence labelled `imported` (`--dry-run` previews) |
| `python tools/build_observations_db.py` | Build the normalised SQL analysis copy `data/observations.db` (`--force` rebuilds; see [docs/OBSERVATIONS_DB.md](docs/OBSERVATIONS_DB.md)) |
| `python main.py seed` | Load the example 30-day dataset into its own database (`seed.db_path`) |
| `python tools/purge_synthetic_data.py` | Report (then remove) simulator/demo rows that got into the car's database (`--apply`) |
| `python main.py guard-test` | Verify the read-only UDS guard |
| `python main.py doctor` | **Work out why the adapter is not connecting** |
| `python main.py sync-cloud` | Upload pending measurements to the cloud ingest API |
| `python main.py prune --days 90` | Drop stale raw response bytes, keeping every parsed value |
| `python main.py prune --days 90 --hard` | Delete history rows older than 90 days entirely |
| `python main.py backup` | Consistent database snapshot (safe while a collector runs) |
| `python main.py unsupported-dids` | List the DIDs the collector has stopped polling (`--clear` to poll them again) |

### Database growth

An always-on collector appends a few hundred rows a minute, so an unattended
database grows without bound. `prune` never runs automatically — deleting data
is your call. The default drops only the verbatim vehicle bytes older than
`--days`, keeping every parsed value, so trends and reports are unaffected.
`--hard` also deletes the rows, and is the only way to reclaim the space
promptly (it vacuums). Vehicles, ECUs, sessions, DTCs and generated reports are
never pruned — and neither is `tx_log`, the safety manifest that records every
request ever sent to the vehicle. That record exists to be kept, so neither
prune mode touches it.

### Retired DIDs

The collector stops re-asking for a DID when the car refuses it outright
(NRC 0x31) or never answers it at all. That list is per-vehicle;
`python main.py unsupported-dids` prints it, keyed to the decoder name, with
when each was dropped. `--clear` empties it so everything is polled again
(`--ecu bat_mgmt` limits it to one module), and DIDs the car really does not
implement retire again on their own.

A silent DID only counts toward retirement when its module answered something
in the same rotation. A module that said nothing at all was unreachable — a
sleeping car, a dropped Bluetooth link, or an adapter that has stopped reaching
part of the bus — and "we never got there" is not evidence that the DID is
unsupported. On 2026-10-17 the collector could not reach any 29-bit module
(BMS, DC-DC, vehicle-info, electric drive) for a whole cycle while the 11-bit
modules answered normally, and 72 DIDs were retired in that one pass —
odometer, VIN and gear selector among them. Every one answered again when
re-read, which is why the guard exists.

### Logs

`data/logs/<component>.log` rotates at 2 MB with three backups kept, so a
collector left running for months cannot fill the disk and the recent past is
always still on disk. The names match what the shortcuts tell you to look at:
`collector.log`, `web.log`, `simulator.log`, `discover.log`. Short-lived
commands (`guard-test`, `prune`, `backup`) share `maintenance.log`.

`tools/start_scanner.py` separately captures each detached process's raw
output — tracebacks and uvicorn's own handler — to `data/logs/<name>.out`,
which it archives rather than appends to forever. The two are deliberately not
the same file: the rotating log is the one to read.

### Backups

Nothing here backs itself up, so run `python main.py backup` before doing
anything irreversible (a `--hard` prune, a schema change) and periodically
afterwards. It uses SQLite's online backup API, so it is safe to run while a
collector is writing: the result is a single consistent snapshot rather than a
file copy that would silently miss everything still sitting in the write-ahead
log. Snapshots are timestamped (`data/backups/real_car-<UTC>.db`) so one bad
day cannot overwrite the last good copy. Change `database.backup_dir` in
`config.local.yaml` to keep them on a different drive.

### Cloud logging

The collector and the Android app can both mirror their measurements into one
shared MySQL/MariaDB database, so a reading taken in the car lives in the same
archive as one taken on the laptop. Local SQLite stays the source of truth and
collection never blocks on the cloud: uploads are best-effort batches resumed
from a watermark, and an unreachable cloud costs nothing but a log line.

Neither writer talks to the database. Both POST to a small ingest API deployed
beside MySQL (`cloud/`), which holds the credentials and is the only writer — so
the phone carries a revocable token instead of a database password, and the
database port never has to be open to the internet. Every row is keyed by a
UUID the client assigns locally, and the API ignores an id it already holds, so
retrying an interrupted upload is always safe and cannot duplicate data.

```bat
:: one-off upload of everything not yet mirrored
.venv\Scripts\python main.py sync-cloud
```

Configure it under `cloud` in `config.local.yaml` (URL + token; leave
`enabled: false` to turn it off) and see `cloud/README.md` for deploying the
API. While `cloud.enabled` is true, a running `collect` also uploads in the
background.

Nothing needs to be hosted to try it: `cloud/docker-compose.yml` runs MariaDB
plus the API locally, and `python tools/cloud_local_check.py` proves the whole
path against a real database before any account or server is set up. Where
Docker is not available (it needs admin rights, WSL2 and a reboot on Windows),
`python tools/local_cloud.py up` runs the same MariaDB plus API unprivileged —
see `cloud/README.md`. The settings can also be overridden per run with
`ODB_CLOUD_ENABLED`, `ODB_CLOUD_API_URL` and `ODB_CLOUD_TOKEN`.

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
* The gateway energy-content DIDs (`0x2AB8`, `0x2AB2`) were recovered from a
  third-party static profile, not from a VW document. Their addressing
  (0x710 -> 0x77A), offsets and multipliers were confirmed on 2026-10-15 by a
  passive Bluetooth HCI capture of Car Scanner (see
  `docs/investigation/meb_gateway_energy_passive_capture_20261015.md`). The
  gross-vs-usable meaning still rests on the profile names, not a VW document;
  raw bytes are stored regardless, so they can be re-scaled later.
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
