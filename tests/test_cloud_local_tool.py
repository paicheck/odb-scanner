"""Tests for the Docker-free local cloud stack tool.

Only the parts that do not need a MariaDB install are covered: where it puts
things, the state file it uses to track pids, and the download digest check.
The install / up / down lifecycle is exercised by actually running the tool
against a real server (see cloud/README.md), which needs the MariaDB ZIP and so
is not part of this suite.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import local_cloud  # noqa: E402


def test_the_install_directory_can_be_moved_with_one_variable(monkeypatch, tmp_path):
    # Everything it creates is relative to this, and it defaults outside the
    # repository on purpose (the repo lives in OneDrive).
    monkeypatch.setenv("ODB_LOCAL_CLOUD_DIR", str(tmp_path))
    assert local_cloud.base_dir() == tmp_path


def test_state_entries_survive_a_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(local_cloud, "BASE", tmp_path)
    monkeypatch.setattr(local_cloud, "STATE", tmp_path / "local_cloud.json")
    local_cloud.save_state({"mariadb": {"pid": 1234, "identity": "1:2"}})
    assert local_cloud.load_state() == {"mariadb": {"pid": 1234, "identity": "1:2"}}


def test_a_corrupt_state_file_reads_as_empty_not_a_crash(tmp_path, monkeypatch):
    # A half-written state file must not stop `up`/`down`/`status` from running.
    state = tmp_path / "local_cloud.json"
    state.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(local_cloud, "STATE", state)
    assert local_cloud.load_state() == {}


def test_a_state_entry_without_a_usable_pid_is_dropped(tmp_path, monkeypatch):
    state = tmp_path / "local_cloud.json"
    state.write_text('{"api": {"pid": "not-a-number"}, "mariadb": {"pid": 7}}',
                     encoding="utf-8")
    monkeypatch.setattr(local_cloud, "STATE", state)
    # The unreadable entry is dropped; a readable pid with no recorded identity
    # is kept, because "no identity" is what makes _is_ours refuse to kill.
    assert local_cloud.load_state() == {"mariadb": {"pid": 7, "identity": None}}


def test_down_with_nothing_recorded_is_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(local_cloud, "STATE", tmp_path / "missing.json")
    assert local_cloud.cmd_down(None) == 0
    assert "nothing to stop" in capsys.readouterr().out


def test_the_download_digest_covers_the_whole_file(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"odb")
    assert local_cloud._sha256(path) == hashlib.sha256(b"odb").hexdigest()


def test_the_pinned_download_digest_is_well_formed():
    # A typo here would silently reject every download, or worse, be compared
    # against a truncated value. It is the published SHA-256 for the ZIP.
    assert len(local_cloud.ZIP_SHA256) == 64
    int(local_cloud.ZIP_SHA256, 16)


def test_a_missing_api_dependency_is_reported_by_name():
    # `up` checks this before spawning anything, so a missing database driver
    # is a clear message instead of an API that dies on import.
    assert local_cloud._missing_api_deps(("definitely_not_a_module_xyz",)) == \
        ["definitely_not_a_module_xyz"]


def test_present_api_dependencies_are_not_reported():
    assert local_cloud._missing_api_deps(("sys", "os")) == []
