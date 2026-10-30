#!/usr/bin/env bash
# Install the odb_scanner systemd user units. Run as yourself, not root:
#
#     deploy/linux/install.sh
#
# It copies the dashboard, collector and backup units into
# ~/.config/systemd/user and reloads systemd. It enables nothing: which of
# them run, and when, is your decision, and the commands are printed at the
# end. Anything that needs root (binding the adapter, lingering, groups,
# the firewall) is printed too, never run.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)

if [[ $EUID -eq 0 ]]; then
    echo "Run this as yourself, not root: these are user units." >&2
    exit 1
fi
# The units name the repo as %h/odb_scanner.
if [[ $repo != "$HOME/odb_scanner" ]]; then
    echo "The units expect the repository at ~/odb_scanner; it is at $repo." >&2
    exit 1
fi
if [[ ! -x $repo/.venv/bin/python ]]; then
    echo "Make the venv first:" >&2
    echo "  cd ~/odb_scanner && python3 -m venv .venv && .venv/bin/pip install -r requirements-lock.txt" >&2
    exit 1
fi

unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
mkdir -p "$unit_dir"
install -m 644 "$here/odb-dashboard.service" "$here/odb-collector.service" \
    "$here/odb-backup.service" "$here/odb-backup.timer" "$unit_dir/"
systemctl --user daemon-reload
echo "Installed into $unit_dir:"
echo "  odb-dashboard.service  odb-collector.service  odb-backup.service  odb-backup.timer"

lingering=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)
in_uucp=no
id -nG | tr ' ' '\n' | grep -qx uucp && in_uucp=yes

cat <<EOF

Nothing is enabled yet. To switch each part on:

  systemctl --user enable --now odb-backup.timer        # daily checked backup
  systemctl --user enable --now odb-dashboard.service   # dashboard on port 8000
  systemctl --user enable --now odb-collector.service   # reads the car

Status and logs:  systemctl --user status 'odb-*'   journalctl --user -u odb-collector -f

Needs root (run these yourself):
EOF
if [[ $lingering != yes ]]; then
    echo "  sudo loginctl enable-linger $USER      # run the units at boot, without logging in"
fi
if [[ $in_uucp != yes ]]; then
    echo "  sudo usermod -aG uucp $USER            # access to /dev/rfcomm0 (log in again after)"
fi
cat <<EOF
  sudo install -m 644 $here/odb-rfcomm@.service /etc/systemd/system/
  sudo systemctl enable --now odb-rfcomm@<ADAPTER-MAC>.service   # pair + trust in bluetoothctl first
  sudo ufw allow from <home-subnet> to any port 8000 proto tcp   # e.g. 192.168.1.0/24, for the tablet

See $here/README.md for what each part does.
EOF
