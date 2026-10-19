"""Tests for the local cloud end-to-end check tool.

Only the parts that must be correct BEFORE any network or database is touched
are covered: the target guard, the settings it builds, and the scratch seed.
The connected path itself is exercised by running the tool against the compose
stack, which needs Docker and so is not part of this suite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repository import Repository  # noqa: E402
from tools import cloud_local_check as check_tool  # noqa: E402


def test_a_non_loopback_target_is_refused_without_the_opt_in(capsys):
    # This tool writes rows; pointed at a real deployment by accident it would
    # add fake readings to the archive. The refusal must come first.
    rc = check_tool.main(["--url", "https://odb-cloud.example.com"])
    assert rc == 1
    assert "not a loopback address" in capsys.readouterr().out


def test_an_unreachable_local_api_is_a_setup_error_not_a_failure(capsys):
    # Exit 2 ("the stack is not up") is distinct from 1 ("it is up and broken"),
    # so the operator knows to start compose rather than go hunting.
    rc = check_tool.main(["--url", "http://127.0.0.1:59999"])
    assert rc == 2
    assert "not reachable" in capsys.readouterr().out


def test_target_host_extracts_the_hostname():
    assert check_tool.target_host("http://127.0.0.1:8080") == "127.0.0.1"
    assert check_tool.target_host("https://odb.example.com/x") == "odb.example.com"


def test_settings_point_at_the_ingest_endpoint():
    settings = check_tool.settings_for("http://127.0.0.1:8080/", "tok", "python:x")
    assert settings.upload_url == "http://127.0.0.1:8080/ingest/measurements"
    assert settings.token == "tok"
    assert settings.enabled and settings.verify_tls is False


def test_the_scratch_seed_is_shaped_like_real_measurements(tmp_path, capsys):
    db = tmp_path / "check.db"
    total = check_tool.seed_scratch_database(db, 3)
    with Repository(db) as repo:
        rows = repo.conn.execute(
            "SELECT m.key, m.value, m.text_value, m.raw_response, v.vin "
            "FROM measurements m JOIN vehicles v ON v.id = m.vehicle_id "
            "ORDER BY m.id").fetchall()
    assert total == 5 and len(rows) == 5
    assert all(r["vin"] == check_tool.TEST_VIN for r in rows)
    # Raw bytes preserved, exactly as the real collector writes them.
    assert rows[0]["raw_response"] == "62 2AB8 00"


def test_the_scratch_seed_includes_the_rows_that_are_not_numbers(tmp_path):
    """The check is worthless if it only ever sends rows shaped like floats.

    A text reading and a structured one are what the payload used to get wrong,
    and the check is the only thing that runs the whole path against a real
    database -- so they have to be in it.
    """
    db = tmp_path / "check.db"
    check_tool.seed_scratch_database(db, 1)
    with Repository(db) as repo:
        rows = {r["key"]: r for r in repo.conn.execute(
            "SELECT key, value, text_value FROM measurements")}
    assert rows["cloud_check_gear"]["value"] == "P"
    assert rows["cloud_check_gear"]["text_value"] is None
    structured = rows["cloud_check_json"]
    assert structured["value"] is None
    assert structured["text_value"].startswith("{")


def test_the_test_vin_is_the_advertised_length():
    assert len(check_tool.TEST_VIN) == 17
