"""The dashboard must render every reading the collector can produce.

`measurements.value` has no declared SQLite type, so it holds whatever the
decoder returned: a float for pack voltage, the string "P" for the gear
selector, the VIN string for `vin`. The value column was formatted with
`'%.4g'|format(...)` in both /measurements and /sources, which accepts numbers
only and raised

    TypeError: must be real number, not str

for a text reading. FastAPI turned that into a 500 for the WHOLE page, so one
non-numeric DID took the entire measurements view down with it.

The failure was latent for as long as the modules answering with text were
unreachable: with no `gear`/`vin` row to format, the pages looked healthy. It
surfaced the moment those readings started arriving. These tests pin the
rendering to the value that arrives, not to the value we wish arrived.
"""
import pytest


def test_format_value_renders_numbers_as_before():
    from web.dashboard import _format_value

    assert _format_value(436.375) == "436.4"
    assert _format_value(0) == "0"
    assert _format_value(0.0) == "0"


def test_format_value_does_not_crash_on_text():
    """The exact regression: a string reading must not raise."""
    from web.dashboard import _format_value

    assert _format_value("P") == "P"
    assert _format_value("WVWZZZE1ZMS000001") == "WVWZZZE1ZMS000001"


def test_format_value_shows_missing_as_dash_not_zero():
    """Absent is not zero -- provenance exists to keep those apart."""
    from web.dashboard import _format_value

    assert _format_value(None) == "—"


@pytest.fixture()
def dashboard(tmp_path):
    """A real app over a throwaway DB holding one text reading and one number.

    The registry is the real `meb` profile, so `gear` and `vin` are listed for
    the same reason the car lists them -- a hand-written fake registry could
    omit exactly the keys that broke.
    """
    from config import Config
    from database.repository import Repository
    from web.dashboard import create_app

    db_file = tmp_path / "web_render.db"
    cfg = Config({
        "database": {"path": str(db_file)},
        "vehicle": {"did_profile": "meb"},
        "web": {"host": "127.0.0.1"},
    })
    repo = Repository(str(db_file))
    vid = repo.ensure_vehicle("WVWZZZE1ZMS000001", make="VW", model="ID.3")
    ts = "2026-10-18T14:00:48+00:00"
    common = dict(ecu="veh_info", service="22", pid="F1A1",
                  provenance="reported", decoding_method="identity",
                  doc_status="experimentally determined", raw_response="")
    repo.record_measurement(vid, ts, "gear", value="P", unit="", **common)
    repo.record_measurement(vid, ts, "vin", value="WVWZZZE1ZMS000001",
                            unit="", **common)
    repo.record_measurement(vid, ts, "pack_voltage", value=436.375,
                            unit="V", **common)
    repo.close()  # create_app opens its own connection

    app = create_app(cfg, bind_host="127.0.0.1")
    from fastapi.testclient import TestClient
    with TestClient(app, follow_redirects=True) as client:
        yield client


@pytest.mark.parametrize("path", ["/measurements", "/sources"])
def test_page_renders_with_a_text_valued_reading(dashboard, path):
    response = dashboard.get(path)
    assert response.status_code == 200, response.text[:500]
    # The text readings are shown as themselves, and the numeric one still
    # gets its usual treatment.
    assert "WVWZZZE1ZMS000001" in response.text
    assert "436.4" in response.text


def test_measurement_alias_resolves_to_the_stored_key(dashboard):
    """`accelerator_pedal_pct` is a readable alias, not a second series."""
    response = dashboard.get("/api/measurements/accelerator_pedal_pct")
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert body["key"] == "res_pid_0149"
    assert body["aliases"] == ["accelerator_pedal_pct"]


def test_series_alias_uses_canonical_key_for_observation(dashboard):
    response = dashboard.get("/api/series?key=accelerator_pedal_pct")
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert body["key"] == "res_pid_0149"
    assert body["observation"]
    assert body["observation"][0]["key"] == "res_pid_0149"
