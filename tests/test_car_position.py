"""Car position: the 0x2430 GPS decoder and the overview map.

The decoder follows evDash's CarVWID3 parser and its sample record from
another MEB car; it has not yet been read on this car, so the catalogue keeps
it unverified. The map uses OpenStreetMap tiles only when web.map_tiles is
"osm", and the CSP admits that one origin and nothing else.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402
from decoders.meb import _gps_position  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

EVDASH_SAMPLE = bytes.fromhex(
    "3138B0333627362E3022450000003438B031322733342E33224E000002A1AA")


def _field(text: str) -> bytes:
    raw = text.encode("latin-1")
    return raw + bytes(14 - len(raw))


def test_evdash_sample_decodes_to_its_documented_position():
    assert _gps_position(EVDASH_SAMPLE) == {
        "lat": 48.209528, "lon": 18.601667, "altitude_m": 172}


def test_fields_are_placed_by_hemisphere_not_by_slot():
    # Same data, fields swapped: latitude first. Still the same point.
    swapped = EVDASH_SAMPLE[14:28] + EVDASH_SAMPLE[0:14] + EVDASH_SAMPLE[28:]
    got = _gps_position(swapped)
    assert (got["lat"], got["lon"]) == (48.209528, 18.601667)


def test_south_and_west_are_negative():
    raw = _field("33°52'4.0\"S") + _field("151°12'26.0\"W") + bytes.fromhex("0200")
    got = _gps_position(raw)
    assert got["lat"] < 0 and got["lon"] < 0


@pytest.mark.parametrize("raw", [
    bytes(31),                                  # no fix: all zero
    EVDASH_SAMPLE[:20],                         # truncated
    _field("18°36'6.0\"E") + _field("48°12'34.3\"E") + b"\x02\xa1",  # no latitude
    _field("95°61'0.0\"N") + _field("18°36'6.0\"E") + b"\x02\xa1",   # 61 minutes
])
def test_no_usable_fix_is_none_not_a_point(raw):
    assert _gps_position(raw) is None


def test_registered_on_the_gps_module_as_a_slow_read():
    spec = build_default_registry("meb").get("gps_position")
    assert (spec.ecu_key, spec.did, spec.slow) == ("gps", 0x2430, True)


# -- overview -----------------------------------------------------------------
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _fix(repo, vid, hours_ago, lat, lon, prov="reported"):
    repo.record_measurement(
        vid, (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
        "gps_position", "gps", "UDS-0x22", "0x2430", "", prov, "builtin",
        "experimental", "", None, json.dumps({"lat": lat, "lon": lon,
                                               "altitude_m": 170}))


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "p.db")
    r = Repository(path)
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    _fix(r, vid, 30, 48.20, 18.60)
    _fix(r, vid, 2, 48.21, 18.61)
    _fix(r, vid, 1, 51.5, -0.1, prov="imported: Car Scanner CSV")   # never shown
    _fix(r, vid, 1, 40.0, -3.7, prov="simulated")                    # never shown
    r.conn.commit()
    r.close()
    return path


def _client(path, tiles):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": path}, "web": {"host": "127.0.0.1",
                                                      "map_tiles": tiles}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"))


def test_positions_use_only_the_cars_own_fixes(db):
    from analysis.position import positions
    r = Repository(db)
    vid = r.first_vehicle()["id"]
    got = positions(r, vid, 30)
    r.close()
    assert (got["latest"]["lat"], got["latest"]["lon"]) == (48.21, 18.61)
    assert [(f["lat"], f["lon"]) for f in got["trail"]] == [(48.2, 18.6), (48.21, 18.61)]


def test_overview_map_with_osm_tiles(db):
    r = _client(db, "osm").get("/")
    assert 'id="position-map"' in r.text and "48.21000, 18.61000" in r.text
    assert "/static/vendor/leaflet/leaflet.js" in r.text
    csp = r.headers["content-security-policy"]
    assert "img-src 'self' data: https://tile.openstreetmap.org;" in csp


def test_tiles_off_requests_nothing_from_outside(db):
    r = _client(db, "off").get("/")
    assert 'id="position-map"' not in r.text and "48.21000, 18.61000" in r.text
    assert "tile.openstreetmap.org" not in r.headers["content-security-policy"]


def test_no_fix_says_so(tmp_path):
    path = str(tmp_path / "e.db")
    r = Repository(path)
    r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    r.close()
    body = _client(path, "osm").get("/").text
    assert "No GPS position recorded yet" in body
    assert 'id="position-map"' not in body
