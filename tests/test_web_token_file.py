"""A generated dashboard token is added to config.local.yaml, not re-dumped over it.

config.local.yaml holds every key the machine has, with comments that explain
them. Persisting a generated token used to yaml.safe_dump the whole file --
every comment gone -- with a truncate-then-write that a crash could leave
empty, and it logged the full ?k=<token> link, which the access-log redaction
does not cover. Now the token is one inserted line, the result is re-read and
must equal the old settings plus the token, the file is replaced atomically
and owner-only, and the log carries a masked token.
"""
import logging
import os
import sys

import pytest
import yaml

from config import load_config

LOCAL = """\
# Machine-specific settings -- keep this comment.
web:
    # the opening map view
    map_default_view: [51.0, -1.0, 13]
database:
    path: /somewhere/car.db   # outside any synced folder
routing:
    ors_api_key: "not-a-real-key"
"""


def _cfg(tmp_path, local_text: str | None):
    (tmp_path / "c.yaml").write_text(
        f"database:\n  path: {tmp_path / 'd.db'}\n"
        "web:\n  host: \"0.0.0.0\"\n  port: 8000\n", encoding="utf-8")
    if local_text is not None:
        (tmp_path / "local.yaml").write_text(local_text, encoding="utf-8")
    return load_config(tmp_path / "c.yaml", tmp_path / "local.yaml")


@pytest.fixture(autouse=True)
def _no_env_token(monkeypatch):
    monkeypatch.delenv("ODB_TOKEN", raising=False)


def test_the_token_is_one_added_line_and_everything_else_is_kept(tmp_path):
    from web.auth import resolve_token
    cfg = _cfg(tmp_path, LOCAL)
    token = resolve_token(cfg, "0.0.0.0")

    text = (tmp_path / "local.yaml").read_text(encoding="utf-8")
    added = [ln for ln in text.splitlines() if ln not in LOCAL.splitlines()]
    assert added == [f'    auth_token: "{token}"'], "more than the token changed"
    before = yaml.safe_load(LOCAL)
    before["web"]["auth_token"] = token
    assert yaml.safe_load(text) == before
    assert "# Machine-specific settings -- keep this comment." in text
    assert resolve_token(cfg, "0.0.0.0") == token, "not reused on restart"


def test_a_file_without_a_web_section_gets_one(tmp_path):
    from web.auth import resolve_token
    cfg = _cfg(tmp_path, "# only a comment\nrouting:\n  countries: GB")
    token = resolve_token(cfg, "0.0.0.0")
    data = yaml.safe_load((tmp_path / "local.yaml").read_text(encoding="utf-8"))
    assert data == {"routing": {"countries": "GB"}, "web": {"auth_token": token}}


def test_no_file_yet(tmp_path):
    from web.auth import resolve_token
    token = resolve_token(_cfg(tmp_path, None), "0.0.0.0")
    assert yaml.safe_load((tmp_path / "local.yaml").read_text(encoding="utf-8")) \
        == {"web": {"auth_token": token}}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_file_is_owner_only(tmp_path):
    from web.auth import resolve_token
    resolve_token(_cfg(tmp_path, LOCAL), "0.0.0.0")
    assert (os.stat(tmp_path / "local.yaml").st_mode & 0o777) == 0o600


def test_a_failed_write_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    from web.auth import resolve_token

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("web.auth.os.replace", refuse)
    token = resolve_token(_cfg(tmp_path, LOCAL), "0.0.0.0")
    assert token, "the dashboard must still get a token for this run"
    assert (tmp_path / "local.yaml").read_text(encoding="utf-8") == LOCAL
    assert not (tmp_path / "local.yaml.tmp").exists(), "temporary file left"


def test_a_layout_the_line_cannot_go_into_is_left_alone(tmp_path):
    """A flow-style web: {...} cannot take an inserted line; the re-read
    check catches it and nothing is written."""
    from web.auth import resolve_token
    flow = "web: {map_default_view: [51.0, -1.0, 13]}\nrouting:\n  countries: GB\n"
    assert resolve_token(_cfg(tmp_path, flow), "0.0.0.0")
    assert (tmp_path / "local.yaml").read_text(encoding="utf-8") == flow


def test_the_token_is_not_logged(tmp_path, caplog):
    from web.auth import masked, resolve_token
    with caplog.at_level(logging.DEBUG, logger="web.auth"):
        token = resolve_token(_cfg(tmp_path, LOCAL), "0.0.0.0")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in text
    assert masked(token) in text and "local.yaml" in text
