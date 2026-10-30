"""The Linux units in deploy/linux/ stay wired to real commands.

The systemd units are the Linux counterpart of the .bat shortcuts
(tests/check_bat_wrappers.py): nothing runs them in CI, so a renamed command or
a moved repository would only show up as a service that silently does nothing.
The collector's restart settings are pinned because a default start limit is
what used to leave it stopped for good, and install.sh must never enable a
unit or run anything as root -- those are the owner's steps.
"""
import configparser
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "linux"
USER_UNITS = ["odb-dashboard.service", "odb-collector.service",
              "odb-backup.service"]


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str           # systemd keys are case-sensitive
    parser.read(DEPLOY / name, encoding="utf-8")
    return parser


@pytest.mark.parametrize("name", USER_UNITS)
def test_user_units_run_a_real_command_from_the_repository(name):
    from main import build_parser
    choices = next(a.choices for a in build_parser()._actions
                   if a.dest == "command")
    service = _unit(name)["Service"]
    assert service["WorkingDirectory"] == "%h/odb_scanner"
    m = re.fullmatch(r"%h/odb_scanner/\.venv/bin/python main\.py (\S+)",
                     service["ExecStart"])
    assert m, service["ExecStart"]
    assert m.group(1) in choices, f"{name} runs unknown command {m.group(1)!r}"


def test_the_collector_restarts_forever_and_stops_cleanly():
    unit = _unit("odb-collector.service")
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"
    assert unit["Service"]["Restart"] == "on-failure"
    assert int(unit["Service"]["RestartSec"]) >= 10
    # cmd_collect handles KeyboardInterrupt: closes the adapter, stops the
    # cloud uploader, then the database.
    assert unit["Service"]["KillSignal"] == "SIGINT"


def test_the_backup_timer_drives_the_backup_service():
    timer = _unit("odb-backup.timer")
    assert timer["Timer"]["Persistent"] == "true"
    assert (DEPLOY / "odb-backup.service").exists()
    assert _unit("odb-backup.service")["Service"]["Type"] == "oneshot"


def test_the_rfcomm_unit_binds_and_releases_the_same_device():
    service = _unit("odb-rfcomm@.service")["Service"]
    assert service["ExecStart"] == "/usr/bin/rfcomm bind 0 %I 1"
    assert service["ExecStop"] == "/usr/bin/rfcomm release 0"


def test_install_script_enables_nothing_and_runs_nothing_as_root():
    script = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    # Drop the heredocs and echo lines: they only print instructions.
    executed = re.sub(r"<<EOF\n.*?\nEOF\n", "<<EOF\n", script, flags=re.S)
    for line in executed.splitlines():
        code = line.split("#", 1)[0].strip()
        if not code or code.startswith("echo "):
            continue
        assert not re.search(r"\bsudo\b", code), f"runs as root: {line!r}"
        assert not re.search(r"\b(enable|start)\b", code), f"switches on: {line!r}"
    for name in USER_UNITS + ["odb-backup.timer"]:
        assert name in script, f"install.sh does not install {name}"
