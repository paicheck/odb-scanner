# Running odb_scanner on Linux

Everything needed to run the collector, dashboard and backups as services on
the Linux PC (written for Arch; package names for Debian/Ubuntu are in
CLAUDE.md). `install.sh` installs the user units and enables nothing; every
step that switches something on, or needs root, is listed below and printed by
the script.

| File | What it is |
|---|---|
| `odb-collector.service` | User unit: `main.py collect`. Waits for a missing adapter, parks while the car sleeps, never hits the restart limit, stops with SIGINT so the adapter is closed cleanly |
| `odb-dashboard.service` | User unit: `main.py serve` on `web.host:web.port` |
| `odb-ingest.service` | User unit: `main.py ingest`, the ingest API on SQLite (`ingest.db_path`) -- the archive this PC's collector uploads to, and the phones once HTTPS is in front of it (`cloud/README.md`, "On this PC") |
| `odb-backup.service`, `odb-backup.timer` | Daily `main.py backup`: integrity-checked snapshot of the car's database and the ingest archive, copied to `database.backup_mirror_dir` when that drive is plugged in, newest `database.backup_keep` kept. Catches up after the laptop was off |
| `odb-rfcomm@.service` | **System** unit: `rfcomm bind 0 <MAC> 1` at boot, so `/dev/rfcomm0` exists |
| `install.sh` | Copies the user units to `~/.config/systemd/user` and reloads systemd |

## Setting it up, in order

1. **Packages** (Arch):

   ```bash
   sudo pacman -S --needed bluez bluez-utils bluez-deprecated-tools tk
   ```

   `bluez-utils` gives `bluetoothctl`, `bluez-deprecated-tools` gives
   `rfcomm`, `tk` lets the GUI tests run.

2. **Groups and lingering.** On Arch `/dev/rfcomm*` belongs to group `uucp`
   (there is no `dialout`). Lingering starts your user services at boot,
   without a login.

   ```bash
   sudo usermod -aG uucp $USER          # then log out and in again
   sudo loginctl enable-linger $USER
   ```

3. **Pair the adapter** (car switched on, adapter plugged in):

   ```text
   bluetoothctl
   [bluetooth]# scan on          # note the adapter's address
   [bluetooth]# pair AA:BB:CC:DD:EE:FF
   [bluetooth]# trust AA:BB:CC:DD:EE:FF
   [bluetooth]# quit
   ```

4. **Bind it at boot:**

   ```bash
   sudo install -m 644 deploy/linux/odb-rfcomm@.service /etc/systemd/system/
   sudo systemctl enable --now odb-rfcomm@AA:BB:CC:DD:EE:FF.service
   ls -l /dev/rfcomm0               # crw-rw---- root uucp
   ```

   `config.local.yaml` needs `adapter: {port: /dev/rfcomm0}`. Then
   `.venv/bin/python main.py doctor` checks the adapter (this talks to it).

5. **Install and switch on the services:**

   ```bash
   deploy/linux/install.sh
   systemctl --user enable --now odb-backup.timer
   systemctl --user enable --now odb-dashboard.service
   systemctl --user enable --now odb-ingest.service
   systemctl --user enable --now odb-collector.service
   ```

6. **Let the tablet reach the dashboard.** `ufw` is on and refuses incoming
   connections by default. Allow port 8000 from the home network only, so the
   dashboard is not offered on every Wi-Fi the laptop joins:

   ```bash
   sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp   # your subnet
   ```

## Day to day

```bash
systemctl --user status 'odb-*'              # what is running
journalctl --user -u odb-collector -f        # follow the collector
systemctl --user list-timers odb-backup      # when the next backup runs
systemctl --user restart odb-dashboard       # after editing config
```

The application also keeps its own rotating logs in `data/logs/`.

## Worth knowing

- **The parked car.** The collector is meant to run all the time. When the car
  sleeps it sends the car nothing and reads only the adapter's own voltage
  (`ATRV`) every `collector.parked_probe_interval` seconds, resuming above
  `collector.wake_voltage`; see "When the car is parked" in the main README.
  Check once at the car that `ATRV` reads below 13.5 V when the car is locked
  and asleep, and above it while charging (`tools/elm_console.py`, type `ATRV`).
- **The backup mirror only works while the drive is mounted.** Desktop
  automounting (`/run/media/$USER/...`) happens in a logged-in session; at a
  boot with no login, the mirror is skipped and the backup stays on this
  machine. To mount the drive at boot regardless, give it an `/etc/fstab`
  entry with `nofail` and point `database.backup_mirror_dir` at that mount.
- **Alerts.** New fault codes, the 12 V battery low while parked, a failed
  backup, a filling disk and finished charges are logged as `ALERT ...` in the
  journal. To get them on the phone, install the ntfy app, subscribe to a long
  random topic, and put `notify: {ntfy_url: https://ntfy.sh/<topic>}` in
  `config.local.yaml` (the topic is a secret: anyone who knows it can read it).
- **Nothing here touches the phones.** Delivering an app update is still
  "build the APK, `adb push` it to the phone's Download folder" (CLAUDE.md).
