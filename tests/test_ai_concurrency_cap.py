"""POST /ai/ask runs at most ollama.max_concurrent generations at once.

Each generation holds a worker thread for up to ollama.timeout; with no cap a
few dozen concurrent asks occupied AnyIO's whole pool and hung every page.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_a_second_concurrent_ask_gets_503(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ai.service import AnalysisService
    from config import Config
    from web.dashboard import create_app

    started, release = threading.Event(), threading.Event()

    def slow_ask(self, question):
        started.set()
        release.wait(10)
        return {"report": "ok", "warnings": []}
    monkeypatch.setattr(AnalysisService, "ask", slow_ask)
    cfg = Config({"database": {"path": str(tmp_path / "c.db")},
                  "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"))

    first = {}
    t = threading.Thread(target=lambda: first.setdefault(
        "r", client.post("/ai/ask", data={"question": "one"})))
    t.start()
    assert started.wait(10)
    second = client.post("/ai/ask", data={"question": "two"})
    release.set()
    t.join(10)
    assert second.status_code == 503
    assert first["r"].status_code == 200
    # The slot is released afterwards.
    assert client.post("/ai/ask", data={"question": "three"}).status_code == 200
