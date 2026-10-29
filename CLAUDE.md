# odb_scanner — working context

Read this first. It is the state of the project as of **2026-10-29**, the rules
that are not negotiable, the everyday commands, and the checklist for moving
the PC from Windows to Linux (in progress on that date).

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
.venv/bin/python -m pytest -q          # ~1,320 tests, ~3.5 min, all must pass
.venv/bin/python -m ruff check .       # must be clean
.venv/bin/python main.py doctor        # adapter checks (owner only: talks to the adapter)
.venv/bin/python main.py serve         # dashboard on :8000 (0.0.0.0 needs web.auth_token)
.venv/bin/python main.py collect       # collector (owner only: talks to the car)
.venv/bin/python tools/smoke_test.py --db /tmp/smoke.db   # whole pipeline against the simulator
.venv/bin/python main.py backup        # consistent snapshot into database.backup_dir

cd android && ./gradlew testDebugUnitTest assembleDebug   # 119 JVM tests; APK in
# ~/odb-scanner-build/app/outputs/apk/debug/app-debug.apk (build dir is outside the repo)
```

The `*.bat` launchers in the repo root and `tools/start_scanner.py` are the
Windows way to run things; on Linux use the `main.py` commands above, under
systemd (below).

## Configuration and keys

`config.yaml` is shared and tracked; personal settings go in
`config.local.yaml` (gitignored, deep-merged over it). Keys it holds — names
only: `web.auth_token`, `database.path`, `database.backup_dir`,
`cloud.enabled/api_url/token`, `routing.ors_api_key`, `routing.ocm_api_key`,
`routing.countries`, optionally `routing.mapbox_token`, `ai.provider/api_key`.
The phones hold their own keys (Route → Settings: OpenRouteService,
OpenChargeMap, Mapbox; Assistant → Settings: the AI service), encrypted.

## Moving the PC to Linux — checklist

**Before wiping Windows** — none of these are in Git; copy them to a USB stick
or cloud storage:

1. The car's database: `C:\Users\kings\odb_data\odb_scanner.db` (~11 MB; the
   only copy of the car's real history). Stop the collector first, or run
   `python main.py backup`.
2. `config.local.yaml` from the project folder (every key and token).
3. `data\` from the project folder (~420 MB: `real_car.db` — the evidence file
   the docs cite — `observations.db`, `backups\`, `logs\`).
4. **The app signing key: `C:\Users\kings\.android\debug.keystore`.** Both
   phones' app is signed with it; without it, APKs built on Linux will not
   install over the existing app, and the only way round is uninstalling —
   which deletes the phone's readings.
5. Optional: `C:\Users\kings\Documents\CarScanner_Investigation`,
   `CarScanner_Capture`, `OBDeleven_Investigation_*` (research captures).

**After installing Linux:**

1. Packages (Debian/Ubuntu names): `git python3 python3-venv bluez
   bluez-deprecated-tools` (for `rfcomm`; on some distros it is in `bluez`),
   `adb` (or the Android SDK platform-tools), `openjdk-17-jdk`.
2. `git clone https://github.com/paicheck/odb-scanner.git ~/odb_scanner`, make
   the venv, restore `config.local.yaml` and `data/` into it, and the database
   to e.g. `~/odb_data/odb_scanner.db`.
3. Edit `config.local.yaml` for Linux paths: `database.path:
   /home/<user>/odb_data/odb_scanner.db` (and `backup_dir`), and add
   `adapter: {port: /dev/rfcomm0}` (config.yaml says `COM3`).
4. Adapter: `bluetoothctl` → `scan on`, `pair <MAC>`, `trust <MAC>`; then
   `sudo rfcomm bind 0 <MAC> 1` gives `/dev/rfcomm0` (make it persistent with a
   small systemd unit or `/etc/bluetooth/rfcomm.conf`). Add the user to
   `dialout`: `sudo usermod -aG dialout $USER`. Then `main.py doctor`.
5. Run `pytest` and `ruff` — both should pass unchanged on Linux.
6. Start at boot with user services (`loginctl enable-linger $USER` so they run
   without a login), e.g. `~/.config/systemd/user/odb-dashboard.service`:

   ```ini
   [Unit]
   Description=odb_scanner dashboard
   [Service]
   WorkingDirectory=%h/odb_scanner
   ExecStart=%h/odb_scanner/.venv/bin/python main.py serve
   Restart=on-failure
   [Install]
   WantedBy=default.target
   ```

   and the same with `main.py collect` (`After=bluetooth.target`) for the
   collector. `systemctl --user enable --now odb-dashboard odb-collector`.
7. Put the saved `debug.keystore` at `~/.android/debug.keystore` **before the
   first Android build** (a build creates a new one if it is missing).
8. Android builds: Android Studio (or the command-line SDK) with
   `ANDROID_HOME` set; `android/local.properties` gets `sdk.dir=`. Use JDK 17:
   AGP 8.5's lint cannot read Java 25 (Android Studio's bundled JBR), which is
   why lint never ran on Windows. Phones over USB need USB debugging and, on
   Linux, the user in `plugdev` (or the distro's android-udev rules).

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
