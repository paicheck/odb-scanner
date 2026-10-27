"""The AI path: bounded requests, contained failures, a validator that bites.

Found in review: no num_ctx/num_predict (Ollama truncates the evidence
silently), HTTP/JSON failures escaping as non-OllamaError (a 500 page), a
naive/aware timestamp TypeError in the charging correlation (also a 500), and
a validator whose BMS pattern could never match lowercased text, whose section
check was a substring test, and whose missing-section warnings never reached
the stored report.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.ollama import TRUNCATED_MARKER, OllamaClient, OllamaError  # noqa: E402
from ai.reports import validate_report  # noqa: E402

GOOD = ("VEHICLE DIAGNOSTIC REPORT\nOBSERVATION\nPack voltage is 402 V.\n"
        "HYPOTHESES\n1. Normal resting voltage.\nLIMITATIONS\nNo load data.")


# -- validator ---------------------------------------------------------------
@pytest.mark.parametrize("claim", [
    "The BMS is defective.",
    "The high-voltage battery is defective.",
    "the inverter is failing",
    "This clearly confirms the charger caused it.",
])
def test_overclaims_are_caught(claim):
    amended, warnings = validate_report(GOOD + "\n" + claim)
    assert warnings, claim
    assert "AUTOMATIC CAVEATS" in amended


def test_a_well_formed_hedged_report_passes():
    assert validate_report(GOOD)[1] == []


def test_sections_must_be_headings_not_words_in_a_sentence():
    text = "OBSERVATION\nThere are no hypotheses and no limitations to state."
    amended, warnings = validate_report(text)
    assert any("LIMITATIONS" in w for w in warnings)
    assert any("HYPOTHESES" in w for w in warnings)
    # ...and they are in the text that is stored and shown in the history.
    assert "missing a LIMITATIONS section" in amended


def test_markdown_headings_count():
    text = "## Hypotheses\n1. x\n**LIMITATIONS:**\nnone known"
    assert validate_report(text)[1] == []


# -- Ollama client -----------------------------------------------------------
class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _client(monkeypatch, resp, sent=None):
    import requests

    def fake_post(url, json=None, timeout=None):
        if sent is not None:
            sent.append(json)
        return resp
    monkeypatch.setattr(requests, "post", fake_post)
    return OllamaClient("http://ollama", "m", num_ctx=8192, num_predict=512)


def test_limits_are_sent(monkeypatch):
    sent = []
    _client(monkeypatch, _Resp(body={"response": "ok"}), sent).generate("p")
    assert sent[0]["options"]["num_ctx"] == 8192
    assert sent[0]["options"]["num_predict"] == 512


@pytest.mark.parametrize("resp", [
    _Resp(status=500, text="runner crashed"),
    _Resp(status=200, body=None, text="<html>proxy</html>"),
    _Resp(status=200, body=["not", "a", "dict"]),
])
def test_http_and_json_failures_are_ollama_errors(monkeypatch, resp):
    with pytest.raises(OllamaError):
        _client(monkeypatch, resp).generate("p")


def test_truncated_output_is_marked_and_flagged(monkeypatch):
    text = _client(monkeypatch, _Resp(body={
        "response": GOOD, "done_reason": "length"})).generate("p")
    assert TRUNCATED_MARKER in text
    assert any("cut off" in w for w in validate_report(text)[1])


# -- charging correlation ----------------------------------------------------
def test_naive_and_aware_timestamps_do_not_raise(repo):
    from analysis.charging import correlate_dtc_with_sessions
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    now = datetime.now(timezone.utc)
    sid = repo.open_session(vid, (now - timedelta(hours=3)).isoformat(), "AC", 40.0)
    # An importer-style naive end time next to aware DTC timestamps.
    repo.conn.execute("UPDATE charging_sessions SET ended_at=?, "
                      "status='completed' WHERE id=?",
                      ((now - timedelta(hours=2)).replace(tzinfo=None)
                       .isoformat(timespec="seconds"), sid))
    repo.conn.commit()
    repo.upsert_dtc(vid, (now - timedelta(hours=1)).isoformat(timespec="seconds"),
                    "bat_mgmt", "P0A0F00", "d", ["powertrain"])
    result = correlate_dtc_with_sessions(repo, vid, days=7)
    assert result["codes_linked_to_charging"] == ["P0A0F00"]


# -- web ---------------------------------------------------------------------
def test_ai_ask_shows_a_non_ollama_failure_instead_of_500(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ai.service import AnalysisService
    from config import Config
    from web.dashboard import create_app

    def boom(self, question):
        raise TypeError("can't compare offset-naive and offset-aware datetimes")
    monkeypatch.setattr(AnalysisService, "ask", boom)
    cfg = Config({"database": {"path": str(tmp_path / "a.db")},
                  "web": {"host": "127.0.0.1"}})
    c = TestClient(create_app(cfg, bind_host="127.0.0.1"))
    r = c.post("/ai/ask", data={"question": "what happened?"})
    assert r.status_code == 200
    assert "Report generation failed" in r.text
