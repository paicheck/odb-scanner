"""`main.py backup` checks every snapshot, mirrors it, and prunes only its own.

It runs unattended from a systemd timer, so a snapshot nobody checked would be
a hope rather than a backup: each one is integrity-checked first, and a failed
check returns 1 with nothing pruned or mirrored. The mirror is usually an
external drive, so a missing mirror folder is skipped and never created.
"""
from pathlib import Path

from config import Config
from database.repository import Repository

SYNTHETIC_VIN = "WVWZZZE1ZMS000001"


def _setup(tmp_path, **database) -> tuple[Config, Path]:
    db = tmp_path / "car.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle(SYNTHETIC_VIN)
        repo.record_measurement(
            vid, "2026-10-05T18:00:00+00:00", "soc_abs", "bat_mgmt",
            "UDS-0x22", "0x028C", "%", "reported", "identity",
            "experimentally determined", "AB", value=50.0)
    cfg = Config({"database": {"path": str(db), "backup_dir": "backups",
                               **database}})
    return cfg, tmp_path / "backups"


def _old_snapshots(folder: Path, n: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for day in range(1, n + 1):
        p = folder / f"car-202610{day:02d}T000000Z.db"
        p.write_bytes(b"older snapshot")
        made.append(p)
    return made


def test_a_snapshot_is_checked(tmp_path, capsys):
    from main import cmd_backup
    cfg, folder = _setup(tmp_path)
    assert cmd_backup(cfg) == 0
    out = capsys.readouterr().out
    assert "integrity check: ok" in out
    (snapshot,) = folder.glob("car-*.db")
    # Checked read-only and immutable: no sidecar files beside the snapshot.
    assert sorted(p.name for p in folder.iterdir()) == [snapshot.name]


def test_a_corrupt_snapshot_fails_with_nothing_pruned_or_mirrored(
        tmp_path, capsys, monkeypatch):
    from main import cmd_backup
    cfg, folder = _setup(tmp_path)
    old = _old_snapshots(folder, 3)
    mirror = tmp_path / "drive"
    mirror.mkdir()

    def broken_backup(self, dest):
        Path(dest).write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
        return Path(dest)

    monkeypatch.setattr(Repository, "backup", broken_backup)
    assert cmd_backup(cfg, keep=1, mirror=str(mirror)) == 1
    assert "FAILED ITS INTEGRITY CHECK" in capsys.readouterr().out
    assert all(p.exists() for p in old), "pruned after a failed check"
    assert list(mirror.iterdir()) == [], "mirrored a corrupt snapshot"


def test_keep_prunes_only_its_own_snapshots(tmp_path, capsys):
    from main import cmd_backup
    cfg, folder = _setup(tmp_path)
    old = _old_snapshots(folder, 4)
    unrelated = [folder / "notes.txt", folder / "car-before-import.db",
                 folder / "car.db.premerge-20261016T141900"]
    for p in unrelated:
        p.write_bytes(b"not mine")

    assert cmd_backup(cfg, keep=2) == 0

    kept = sorted(p.name for p in folder.glob("car-2026*Z.db"))
    assert len(kept) == 2
    assert kept[0] == old[-1].name, "did not keep the newest older snapshot"
    assert all(p.exists() for p in unrelated), "pruned a file it did not write"


def test_keep_comes_from_config(tmp_path, capsys):
    from main import cmd_backup
    cfg, folder = _setup(tmp_path, backup_keep=1)
    _old_snapshots(folder, 2)
    assert cmd_backup(cfg) == 0
    assert len(list(folder.glob("car-2026*Z.db"))) == 1


def test_the_mirror_gets_an_identical_copy_and_is_pruned(tmp_path, capsys):
    from main import cmd_backup
    mirror = tmp_path / "drive"
    cfg, folder = _setup(tmp_path, backup_mirror_dir=str(mirror))
    mirror.mkdir()
    _old_snapshots(mirror, 3)

    assert cmd_backup(cfg, keep=2) == 0

    (snapshot,) = folder.glob("car-*.db")
    copy = mirror / snapshot.name
    assert copy.read_bytes() == snapshot.read_bytes()
    assert not list(mirror.glob("*.partial")), "temporary copy left behind"
    assert len(list(mirror.glob("car-2026*Z.db"))) == 2
    assert "mirror: copied to" in capsys.readouterr().out


def test_a_missing_mirror_is_skipped_and_never_created(tmp_path, capsys):
    from main import cmd_backup
    mirror = tmp_path / "media" / "4TB" / "backups"     # drive not plugged in
    cfg, folder = _setup(tmp_path)
    assert cmd_backup(cfg, mirror=str(mirror)) == 0, "the local backup worked"
    assert not mirror.exists() and not mirror.parent.exists()
    assert "skipped" in capsys.readouterr().out
    assert len(list(folder.glob("car-*.db"))) == 1


def test_an_explicit_out_file_is_never_pruned_around(tmp_path, capsys):
    from main import cmd_backup
    cfg, folder = _setup(tmp_path)
    old = _old_snapshots(folder, 2)
    assert cmd_backup(cfg, out=str(tmp_path / "one-off.db"), keep=1) == 0
    assert all(p.exists() for p in old)


def test_keep_below_one_is_refused(tmp_path, capsys):
    from main import cmd_backup
    cfg, folder = _setup(tmp_path)
    assert cmd_backup(cfg, keep=0) == 2
    assert not folder.exists(), "backed up despite refusing the arguments"
