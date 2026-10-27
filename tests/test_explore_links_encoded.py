"""Values placed in the explore page's links are URL-encoded.

They were HTML-escaped only, so a search such as `pack_voltage v` or one with
`&` in it produced broken or injected query parameters in the export and
chart links (not XSS: autoescape still applied).
"""
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402


def test_search_terms_are_url_encoded_in_links(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    db = str(tmp_path / "x.db")
    r = Repository(db)
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    r.record_measurement(vid, datetime.now(timezone.utc).isoformat(
        timespec="seconds"), "pack_voltage", "bat_mgmt", "UDS-0x22", "0x1E3B",
        "V", "reported", "builtin", "experimental", "0690", 420.0)
    r.conn.commit()
    r.close()
    cfg = Config({"database": {"path": db}, "web": {"host": "127.0.0.1"},
                  "vehicle": {"did_profile": "meb"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"))
    html = client.get("/explore", params={"q": "pack_voltage v"}).text
    hrefs = re.findall(r'href="([^"]*q=[^"]*)"', html)
    assert hrefs, "the search produced no links to check"
    assert all("q=pack_voltage%20v" in h for h in hrefs), hrefs
    assert not any("q=pack_voltage v" in h for h in hrefs)
