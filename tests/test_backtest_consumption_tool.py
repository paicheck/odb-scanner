"""tools/backtest_consumption.py: the backtest on a database it only reads."""
import hashlib
import sys
from pathlib import Path

from test_consumption_in_route import _feels_the_cold, _history

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import backtest_consumption  # noqa: E402


def _digest(folder: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.iterdir())}


def test_the_report_reads_and_never_writes(tmp_path, capsys):
    db = tmp_path / "car" / "c.db"
    db.parent.mkdir()
    repo, _ = _history(db, _feels_the_cold)
    repo.close()
    before = _digest(db.parent)
    assert backtest_consumption.main(["--db", str(db), "--drives"]) == 0
    out = capsys.readouterr().out
    assert "Verdict: model" in out and "outside temperature:" in out
    assert "WVWZZZE1ZMS000001" not in out               # no VIN in a report
    assert len([ln for ln in out.splitlines() if ln[:2] == "20"]) == 60
    after = _digest(db.parent)
    assert after["c.db"] == before["c.db"]
    # SQLite's own coordination files for a WAL database may appear beside it;
    # a read-only connection never writes a transaction into them.
    assert set(after) <= {"c.db", "c.db-wal", "c.db-shm"}
    wal = db.parent / "c.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0


def test_a_missing_database_is_an_error_not_a_new_file(tmp_path, capsys):
    db = tmp_path / "none.db"
    assert backtest_consumption.main(["--db", str(db)]) == 2
    assert not db.exists()
