# Project review, 2026-10-29

A whole-project review made right after the PC moved from Windows to Linux
(Arch). It records what was found **on this date**; later commits say what
has been fixed. Items are ranked within each section, most important first.

**Scope.** Reviewed in depth: the read-only guard on both platforms
(`diagnostic/uds.py`, `ReadOnlyGuard.kt`), the ELM327 transport, the
collector loop, dashboard auth/CSRF/escaping, the cloud ingest API, the
database and backups, the Android manifest and Android Auto service, CI.
Not reviewed in depth: the correctness of decoders, analysis, routing and the
Android UI -- those rest on the test suite (1,318 passing on Linux) and a
stricter ruff pass (`B`, `PLE`, `PLW`, `DTZ`, `S110`, `BLE`), which found
nothing real.

**Verdict.** The parts that protect the car and its data are sound: the
allow-list fails closed, requests are validated in the exact form they are
sent, the dashboard token is compared in constant time and kept out of URLs,
CSRF and DNS rebinding are handled, templates escape everything, the cloud
API caps bodies before parsing and inserts idempotently. The weak spots are in
running it **always on, under Linux**: start-up, the parked car, backups, and
the phones' data.

## Bugs

1. **The collector exits if the adapter cannot be reached at start-up.**
   `Collector.run()` calls `open_and_identify()` before its loop, outside the
   error handling ([collector.py](../../collector.py), `run`), and
   `cmd_collect` ([main.py](../../main.py)) does not catch it. Only losses
   *after* a successful start are retried (`_recover_adapter`). Under systemd
   with `Restart=on-failure` and the default start limit (5 in 10 s), a few
   fast failures at boot -- car away, `/dev/rfcomm0` not bound yet -- leave the
   collector stopped for good with nothing visible.
   *Fix:* retry at start with the same back-off as `_recover_adapter`; units
   with `RestartSec=` and `StartLimitIntervalSec=0`.

2. **The Android CI job fails before building.** `android-actions/setup-android@v3`
   runs `sdkmanager` (cmdline-tools 16.0), which prints the Android SDK
   *Preview* licence and exits 1, so `Unit tests` never runs. Every CI run on
   `main` is red because of this job alone since the Ubuntu jobs were fixed
   (`61855bb`).
   *Fix (needs a test push):* pin the cmdline-tools version, or pass no
   default packages and let Gradle install platform 34 with the licences
   accepted.

3. **First-run dashboard token handling** ([web/auth.py](../../web/auth.py),
   `_generate_and_persist`). When no token is configured it (a) logs the full
   `?k=<token>` link at WARNING through `web.auth`, which `RedactTokenFilter`
   does not cover (it is attached to `uvicorn.access` only), so the token
   lands in `data/logs/web.log` and, under systemd, the journal; (b) rewrites
   `config.local.yaml` with `yaml.safe_dump`, dropping every comment; (c)
   writes it non-atomically, so a crash mid-write loses every key in the file.
   Latent while `web.auth_token` is set.
   *Fix:* log a masked token and where it is stored; append the key instead of
   re-dumping; write to a temp file and `os.replace`, mode 0600.

4. **CLAUDE.md's Linux checklist is Debian-specific and stale.** On Arch the
   serial group is `uucp` (udev gives `/dev/rfcomm*` to it; there is no
   `dialout`), USB access to the phones is `adbusers` from `android-udev`
   (not `plugdev`), the packages are `bluez-utils` (bluetoothctl),
   `bluez-deprecated-tools` (rfcomm), `android-tools`, `android-udev`,
   `jdk17-openjdk`, plus `tk` for the GUI tests. "pytest passes unchanged on
   Linux" was not true until `a85382e`..`8a0bc3a`.

## Hardening

1. **Decide the parked-car policy before the collector runs always-on.** A
   sleeping car fails every read, so the cycle counts as dead and
   `_recover_adapter` reconnects with back-off capped at
   `RECONNECT_MAX_BACKOFF_S = 300` -- every five minutes, indefinitely. Each
   reconnect is `ATZ`, the init sequence, the `0100` warm-up and a VIN read on
   the bus. Whether that wakes the gateway (and so the 12 V systems) has not
   been measured. A safer design: once the car is asleep, probe only with
   `ATRV` (the adapter measures its own supply at OBD pin 16; nothing is sent
   on the bus) and resume polling when the voltage shows the DC-DC converter
   running.

2. **Enforce the AT-command allow-list at the transport.** The allow-list
   exists only in [tools/elm_console.py](../../tools/elm_console.py)
   (`is_adapter_command`, tested in `tests/test_elm_console.py`).
   `check_raw` in [tools/diagnose_meb_path.py](../../tools/diagnose_meb_path.py)
   lets any `AT` line through except a non-flow-control `ATFCSD`, and
   `Elm327Transport.send_command` checks nothing beyond line breaks -- so
   `ATMA`, `ATPP` (adapter EEPROM), `ATWM`/`ATRTR` (transmit on their own) are
   one edit away. Move the allow-list into `diagnostic/` and enforce it in
   `send_command`. Also narrow `0x3E` TesterPresent to sub-function `0x00`
   (`READ_SUBFUNCTIONS` has no entry for it, so any sub-function passes), on
   both platforms via the shared spec.

3. **The phones run debug builds.** Delivery uses `app-debug.apk`, so
   `FLAG_DEBUGGABLE` is set and
   [OdbCarAppService.kt](../../android/app/src/main/kotlin/com/odbscanner/car/OdbCarAppService.kt)
   uses `ALLOW_ALL_HOSTS_VALIDATOR`: any app on the phone can bind the Android
   Auto service and receive the battery, route and fault screens. A debuggable
   app's data (database, encrypted keys) is also readable with `run-as` over
   USB debugging. A release build type signed with the **same** key keeps the
   certificate, so it installs as an update with no data loss; check with
   `apksigner verify --print-certs` before copying it over.

4. **Dashboard exposure on a laptop.** `web.host: 0.0.0.0` over plain HTTP:
   the token protects it, but it can be read off any network the laptop joins.
   `ufw` is active. Allow port 8000 from the home subnet only
   (`ufw allow from <subnet> to any port 8000 proto tcp`), or bind to the
   home interface's address.

5. **Backups are manual and unverified.** `main.py backup` uses the online
   backup API (correct) but does not check the snapshot and never prunes old
   ones. Add `--verify` (`PRAGMA integrity_check` on the snapshot), retention,
   a systemd timer, and a copy to the external drive when it is mounted.

6. **Supply chain.** No Dependabot or `pip-audit`; GitHub Actions are pinned by
   tag, not commit SHA.

7. **The car's VIN** is in 34 tracked files, including
   `tests/test_import_carscanner.py`, against the synthetic-VIN rule. Rewriting
   history is the owner's decision; a CI check holding only the VIN's SHA-256
   would stop it spreading to new files either way.

## On the Linux PC (state on this date)

- `cloud.enabled` is true and points at a loopback ingest that came from
  `cloud/docker-compose.yml` on the Windows PC. Docker is not installed here
  and nothing listens, so cloud sync would retry and log warnings. Disable it
  or install Docker/Podman and bring the stack up.
- The Assistant's provider is Ollama, which is not installed.
- The phones' readings have no copy off the phones: `allowBackup="false"`, and
  the cloud stack they could upload to was on the Windows PC.
- Still to install: `bluez-utils`, `bluez-deprecated-tools`, `android-tools`,
  `android-udev`, `jdk17-openjdk`, the Android SDK; user in `uucp` and
  `adbusers`; `loginctl enable-linger`.

## Extensions

1. **Linux deployment kit** (`deploy/linux/`): user units for the dashboard and
   collector (with the start-up retry above), an `rfcomm` bind unit, a backup
   timer, an install script, and the Arch notes in CLAUDE.md.
2. **This PC as the phones' archive.** Run the ingest here and let the phones
   upload over home Wi-Fi -- closes the phone-data gap above.
3. **12 V battery watch.** An overnight voltage trend from `ATRV` (no bus
   traffic, so it does not wake the car) plus the `aux_12v_*` DIDs while the
   car is awake, with an alert below a threshold. 12 V trouble is a known
   ID.3 weakness, and this falls out of hardening item 1.
4. **Notifications** (ntfy or desktop): new fault code, charging stopped
   early, low 12 V, collector silent for hours, backup failed, low disk.
5. **Home charging report:** energy added, the charging curve, and cost from a
   configured tariff.

## Suggested order

Hardening 1 (decide with the owner) → bug 1 with extension 1 → extension 2 →
hardening 3 → the rest. One branch per item, one commit per change, nothing
pushed without the owner's OK.
