"""/investigations no longer echoes ?error= into its error box.

Nothing in the app redirected with that parameter, so its only effect was to
let a crafted link show arbitrary text styled as an application error.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_error_parameter_is_not_reflected(tmp_path):
    from fastapi.testclient import TestClient

    from config import Config
    from web.dashboard import create_app
    cfg = Config({"database": {"path": str(tmp_path / "e.db")},
                  "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"))
    html = client.get("/investigations",
                      params={"error": "Your car is unsafe - call 0800"}).text
    assert "call 0800" not in html
