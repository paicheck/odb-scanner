"""tools/export_catalogue.py renders a database as prose for a reader who will
never see it.

The failure that matters is a report that reads as authoritative while being
wrong about what a number is: a simulator row presented as a vehicle reading,
108 cell slots listed so nothing else can be found, or a maintained finding
(like a mis-scaled decoder) dropped because the generator did not know about
it. These tests pin those.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.repository import Repository  # noqa: E402
from tools import export_catalogue as export  # noqa: E402

CAR_VIN = "WVWZZZE1ZMP087053"


def _measurement(repo, vid, ts, key, ecu, provenance, value=1.0):
    repo.record_measurement(
        vid, ts, key, ecu, "UDS-0x22", "0x028C", "V", provenance, "builtin",
        "experimentally determined", "", value, None, True, None)


def _report(repo, profile="meb"):
    return export.build_report(repo, profile)


def test_every_source_is_named(repo):
    """'reported' and 'simulated' must never look alike: the whole point of the
    file is telling a reader which numbers came off a car."""
    vid = repo.ensure_vehicle(CAR_VIN)
    _measurement(repo, vid, "2026-10-06T12:00:00+00:00", "soc_abs", "bat_mgmt",
                 "reported")
    _measurement(repo, vid, "2026-10-06T12:01:00+00:00", "soc_abs", "bat_mgmt",
                 "simulated")
    repo.conn.commit()
    text = _report(repo)
    assert "reported: 1" in text
    assert "simulated: 1" in text
    # ...and the caveat list says what 'simulated' means.
    assert "simulates" in text or "simulator" in text


def test_cell_slots_are_rolled_up_into_one_line(repo):
    """216 cell keys would bury eight modules' worth of information."""
    vid = repo.ensure_vehicle(CAR_VIN)
    for i in range(108):
        _measurement(repo, vid, "2026-10-06T12:00:00+00:00", f"cell_v_{i:03d}",
                     "bat_mgmt", "reported")
    repo.conn.commit()
    text = _report(repo)
    assert text.count("cell_v_") == 0, "individual cell slots leaked into the list"
    assert "`cell_voltages`" in text
    assert "across 108 slots" in text


def test_a_key_with_no_rows_is_flagged_rather_than_omitted(repo):
    """"Never read" and "read once" are different problems for a reader."""
    vid = repo.ensure_vehicle(CAR_VIN)
    _measurement(repo, vid, "2026-10-06T12:00:00+00:00", "soc_abs", "bat_mgmt",
                 "reported")
    repo.conn.commit()
    text = _report(repo)
    assert "`pack_voltage`" in text          # in the registry, never read here
    assert "**never read**" in text
    assert "0 of" in text


def test_imported_evidence_is_attributed_to_its_tool(repo):
    """Rows the collector did not take are not readings this tool made."""
    vid = repo.ensure_vehicle(CAR_VIN)
    _measurement(repo, vid, "2026-10-06T12:00:00+00:00", "gate_lv_soc",
                 "19.Gate", "imported: Car Scanner CSV")
    repo.conn.commit()
    text = _report(repo)
    assert "Also collected, not in the current registry" in text
    assert "provenance=imported: Car Scanner CSV" in text


def test_the_maintained_findings_are_not_dropped(repo):
    """A generator cannot rediscover that a decoder is mis-scaled."""
    text = _report(repo)
    assert "Known BAD scalings" in text
    assert "windshield_temperature" in text
    assert "float32" in text
    assert "900 s" in text          # the stale carry-forward window


def test_the_wrong_signed_chg_caveat_is_gone(repo):
    """It claimed the charger had 'answered NOTHING since 2026-09-20'.

    Backwards: the only charger rows that ever existed came from the simulator,
    and the real car has been answering the module since 2026-10-15. A caveat
    that points the opposite way is worse than none, because it is believed.
    """
    text = _report(repo)
    assert "answered NOTHING" not in text


def test_an_empty_database_renders_without_crashing(repo):
    text = _report(repo)
    assert "No measurements." in text
    assert "no vehicle in the database" in text


def test_the_cli_writes_the_file(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    with Repository(db) as repo:
        _measurement(repo, repo.ensure_vehicle(CAR_VIN),
                     "2026-10-06T12:00:00+00:00", "soc_abs", "bat_mgmt",
                     "reported")
        repo.conn.commit()
    out = tmp_path / "cat.md"
    assert export.main(["--db", db, "--out", str(out)]) == 0
    assert "Collected data" in out.read_text(encoding="utf-8")
