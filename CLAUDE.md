# odb_scanner — working context

Read this first. It is the state of the project as of **2026-10-30**, the rules
that are not negotiable, the everyday commands, and how the Linux PC is set
up.

## What this is

A strictly **read-only** diagnostic, analysis and trip-planning system for one
2021 VW ID.3 58 kWh, in three parts sharing one measurement spec:

| Part | Where | What |
|---|---|---|
| PC collector + dashboard | `main.py`, `collector.py`, `diagnostic/`, `decoders/`, `database/`, `analysis/`, `web/`, `routing/`, `ai/` | Reads the car over an ELM327 (Veepeak OBDCheck BLE+, Bluetooth SPP), stores every raw response in SQLite, FastAPI dashboard on port 8000 |
| Phone app | `android/` (Kotlin, Compose, Room, Car App Library) | Collects in the car, route planner, turn-by-turn navigation, voice assistant, Android Auto screens |
| Cloud ingest (optional) | `cloud/` | One MySQL archive both writers upload to |

Deeper reading: `README.md` (PC), `android/README.md` (phone), `cloud/README.md`,
`docs/REGRESSION_GUARDS.md`, `docs/PROJECT_HANDOVER.md` (older, 2026-10-26).

## Rules (never break these)

- **Never** open a serial/Bluetooth adapter or talk to the vehicle. Test only
  with stub transports, `tests/` fixtures or the built-in simulator. Only the
  owner runs the collector against the car.
- **Never** read or write the live database (`database.path` in
  `config.local.yaml`) or anything under `data/`. Use temporary databases.
  `python main.py backup` is the only safe way to copy it, and only on request.
- Do not loosen the read-only allow-list (`diagnostic/uds.py`,
  `android/.../uds/ReadOnlyGuard.kt`), the token checks (`web/auth.py`,
  `cloud/api.py`) or any existing safety test.
- Work on a branch, one commit per item. **Push or merge only with the owner's
  OK.** Commit messages end with the Co-Authored-By line used in the history.
- Never print auth tokens or API keys (redact `config.local.yaml` when showing
  it). Keys go in `config.local.yaml` (gitignored) or the phone's settings —
  never in `config.yaml` or any tracked file.
- Keep the home location out of tracked files; tests use a neutral town and
  synthetic VINs (`WVWZZZE1ZMS000001`), never the car's own.
- **Never run instrumented (device) tests on the owner's phones**: they can
  uninstall the app and wipe its data. Use an emulator, created for the run and
  deleted afterwards (disk space is tight).
- Delivering a phone update = build the APK and copy it to the phone's
  Download folder (`adb push … /sdcard/Download/`); the owner taps Update.
  Before updating, check the installed app's signing certificate matches
  (`apksigner verify --print-certs`) — a mismatch would force an uninstall.

## Everyday commands (Linux)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-lock.txt
.venv/bin/python -m pytest -q          # ~1,350 tests, ~3 min, all must pass
.venv/bin/python -m ruff check .       # must be clean
.venv/bin/python main.py doctor        # adapter checks (owner only: talks to the adapter)
.venv/bin/python main.py serve         # dashboard on :8000 (0.0.0.0 needs web.auth_token)
.venv/bin/python main.py collect       # collector (owner only: talks to the car)
.venv/bin/python tools/smoke_test.py --db /tmp/smoke.db   # whole pipeline against the simulator
.venv/bin/python main.py backup        # checked snapshot into database.backup_dir, mirrored, newest kept

cd android && ./gradlew testDebugUnitTest assembleDebug   # 119 JVM tests; APK in
# ~/odb-scanner-build/app/outputs/apk/debug/app-debug.apk (build dir is outside the repo)
```

The `*.bat` launchers in the repo root and `tools/start_scanner.py` are the
Windows way to run things; on Linux use the `main.py` commands above, under
systemd (`deploy/linux/`).

## Configuration and keys

`config.yaml` is shared and tracked; personal settings go in
`config.local.yaml` (gitignored, deep-merged over it). Keys it holds — names
only: `web.auth_token`, `database.path`, `database.backup_dir`,
`database.backup_keep`, `database.backup_mirror_dir`, `adapter.port`,
`cloud.enabled/api_url/token`, `routing.ors_api_key`, `routing.ocm_api_key`,
`routing.countries`, optionally `routing.mapbox_token`, `ai.provider/api_key`.
The phones hold their own keys (Route → Settings: OpenRouteService,
OpenChargeMap, Mapbox; Assistant → Settings: the AI service), encrypted.

## The Linux PC (Arch)

The PC moved from Windows to Arch on 2026-10-29; the Windows disk is gone. What
was carried over is also on the external drive in
`/run/media/steven/4TB/odb_scanner_backup_2026-10-29` (database,
`config.local.yaml`, `data/`, the signing key, research captures) — keep it.

| What | Where |
|---|---|
| Repository | `~/odb_scanner` |
| Car database | `~/odb_data/odb_scanner.db`; daily snapshots in `~/odb_data/backups`, mirrored to `/run/media/steven/4TB/odb_scanner_backups` while the drive is mounted |
| App signing key | `~/.android/debug.keystore`, the Windows one (SHA-256 `d6967d0e…37feee`) |
| Services | `deploy/linux/`: user units for the collector, dashboard and daily backup, a system unit binding the adapter to `/dev/rfcomm0`. How-to: `deploy/linux/README.md` |

**Done:** packages `bluez bluez-utils bluez-deprecated-tools android-tools
android-udev jdk17-openjdk tk`; the user is in `uucp` (Arch gives
`/dev/rfcomm*` to it; there is no `dialout`) and `adbusers` (USB to the phones;
not `plugdev`); lingering is on; the user units are installed but **not
enabled**. On Debian/Ubuntu the names are `python3-venv bluez adb
openjdk-17-jdk` and the groups `dialout`/`plugdev`.

**The owner still has to:**

1. Pair the adapter and bind it: `bluetoothctl` (`pair`, `trust`), then
   `odb-rfcomm@<MAC>.service`; `main.py doctor`. Check `ATRV` once with the
   car asleep and once awake against `collector.wake_voltage` (13.5 V).
2. `systemctl --user enable --now odb-backup.timer odb-dashboard.service
   odb-collector.service`.
3. Let the tablet in: `sudo ufw allow from <home subnet> to any port 8000
   proto tcp`.
4. Android SDK (the licences are the owner's to accept): command-line tools in
   `~/Android/Sdk`, then `platform-tools`, `platforms;android-34`,
   `build-tools;34.0.0`; set `ANDROID_HOME` and `sdk.dir=` in
   `android/local.properties`. Keep JDK 17: AGP 8.5's lint cannot read Java 25.

## State on 2026-10-29

`main` on GitHub is at `b2a4b5d`; everything below is pushed. The 14-commit
`deep-review` work (merged into main) found and fixed, measured on a 2.6M-row
month of synthetic readings:

- Dashboard speed: hourly summary tables (`measurement_hours`,
  `measurement_minutes`; a cache rebuilt by trigger after any delete), one
  covering index (`ix_measurements_series`), linear-time consumption and
  latest-value queries. Overview 115 s → ~2 s, Route 295 s → 0.3 s.
- Phone: the Trends chart showed the oldest values (fixed); Room v4 adds
  indexes (migration tested on an emulator); carried values expire after 900 s.
- Planner consumption ignored gaps (hidden charges) and dropped regeneration.
- New: Trips page (drive log, efficiency by outside temperature), battery
  capacity trend with warranty-line projection, low-disk warning, phone
  navigation speed-limit sign (Mapbox `maxspeed`) and arrival charge.
- Assistant: Gemini/Groq/Mistral free tiers need only a key (default models
  `gemini-3.8-flash` with `reasoning_effort: low`, `llama-3.3-70b-versatile`,
  `mistral-small-latest`); clearer timeout messages.
- Wake word ("hey scanner", off by default): fixed a crash loop — Vosk's
  `SpeechService.shutdown()` released the microphone under its own reading
  thread. `Spotter` in `ai/WakeWord.kt` now owns the recorder on one thread.
  Confirmed working on the Pixel 7 on 2026-10-29.

## Open items

- **Project review, 2026-10-29:**
  `docs/investigation/project_review_20261029.md` lists the bugs, hardening
  and extensions found after the move to Linux, in the order to tackle them.
  Start there.
- **HONOR X8a** still has the 20261029c APK, which has the wake-word crash —
  copy the current APK to it next time it is connected.
- Not yet tried for real: the speed-limit sign and arrival charge on the road
  with a Mapbox token; the Trips page and capacity trend on the car's own data
  (the trend needs 60 days of history).
- The car's VIN is in `config.yaml` and ~30 tracked docs/tests on GitHub;
  removing it means rewriting history — the owner's decision.
- Phones: HONOR X8a (the car phone) and a Pixel 7 (Android beta), both running
  builds signed with the Windows PC's debug key (SHA-256 `d6967d0e…37feee`).
  Check a Linux build carries the same certificate before copying it over.
