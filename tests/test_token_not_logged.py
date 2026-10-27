"""The dashboard access token stays out of stdout and the access log.

`main.py serve` printed the full ?k=<token> URL on every start, and uvicorn's
access log wrote each ?k= request line before the cookie redirect -- both end
up in data/logs/web.out, so any log shared for a bug report carried the token.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "Zr8Qv2Lw0Kp4Yt7Ns1Hd9Fx3Bm6Jc5Ae"


def test_serve_prints_only_a_masked_token(tmp_path, monkeypatch, capsys):
    import uvicorn

    import main
    from config import Config
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    cfg = Config({"database": {"path": str(tmp_path / "s.db")},
                  "web": {"host": "0.0.0.0", "auth_token": TOKEN}})
    assert main.cmd_serve(cfg) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "Access token required" in out


def test_access_log_lines_are_redacted():
    from web.auth import RedactTokenFilter
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.5:5123", "GET", f"/battery?days=7&k={TOKEN}", "1.1", 303),
        None)
    RedactTokenFilter().filter(record)
    line = record.getMessage()
    assert TOKEN not in line
    assert "k=<redacted>" in line and "days=7" in line


def test_create_app_installs_the_filter(tmp_path):
    from config import Config
    from web.auth import RedactTokenFilter
    from web.dashboard import create_app
    create_app(Config({"database": {"path": str(tmp_path / "f.db")}}),
               bind_host="127.0.0.1")
    assert any(isinstance(f, RedactTokenFilter)
               for f in logging.getLogger("uvicorn.access").filters)
