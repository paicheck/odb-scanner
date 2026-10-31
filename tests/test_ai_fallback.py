"""AI fallbacks (ai.fallbacks): when one service fails, the next is asked.

Pinned: the chain is built only when backups are configured, a broken backup
entry never takes the main service down, each failure hands the question on
and the last one says why every service failed, the report records the model
that wrote it and says a backup did, and -- the one that matters most -- the
VIN is removed whenever ANY member could send it off the machine, even when
the first one is the local Ollama.

No test reaches the network. The VIN is synthetic.
"""
import pytest

from ai import cloud
from ai.ollama import AIError, OllamaClient
from config import Config
from database.repository import Repository

VIN = "WVWZZZE1ZTEST0001"          # synthetic, VIN-shaped


def _cfg(**ai):
    return Config({"ai": ai, "ollama": {"host": "http://localhost:11434",
                                        "model": "qwen2.5:7b"}})


class Fake:
    def __init__(self, where, model="m", answer=None, offsite=True, error=None):
        self.where, self.model, self.sends_offsite = where, model, offsite
        self.answer, self.error, self.asked = answer, error, []

    def generate(self, prompt, system=None, temperature=None):
        self.asked.append(prompt)
        if self.error:
            raise AIError(self.error)
        return self.answer

    def available(self):
        return self.error is None


# --- building it ------------------------------------------------------------------

def test_no_fallbacks_is_the_plain_client():
    llm = cloud.llm_from_config(_cfg(provider="gemini", api_key="k"))
    assert isinstance(llm, cloud.OpenAICompatibleClient)


def test_fallbacks_make_a_chain_in_order():
    llm = cloud.llm_from_config(_cfg(
        provider="gemini", api_key="k1",
        fallbacks=[{"provider": "groq", "api_key": "k2"}, "ollama"]))
    assert isinstance(llm, cloud.FallbackChain)
    first, second, third = llm.members
    assert first.preset.label.startswith("Google Gemini") and first.key == "k1"
    assert second.preset.label.startswith("Groq") and second.key == "k2"
    assert second.model == "openai/gpt-oss-120b"          # its own default
    assert isinstance(third, OllamaClient)
    assert llm.where.startswith("Google Gemini") and "then local Ollama" in llm.where


def test_a_broken_backup_entry_is_left_out_not_fatal():
    llm = cloud.llm_from_config(_cfg(
        provider="gemini", api_key="k",
        fallbacks=["no-such-service", 42, {"model": "x"}, "groq"]))
    assert [m.preset.label.split()[0] for m in llm.members] == ["Google", "Groq"]


# --- asking -----------------------------------------------------------------------

def test_each_failure_hands_the_question_on():
    a = Fake("A", error="rate limit reached")
    b = Fake("B", error="Could not reach B")
    c = Fake("C", model="c-1", answer="fine")
    text, by, failed = cloud.FallbackChain([a, b, c]).generate_with_source("q")
    assert (text, by) == ("fine", c)
    assert failed == [("A", "rate limit reached"), ("B", "Could not reach B")]
    assert a.asked == b.asked == c.asked == ["q"]


def test_the_first_answer_wins_and_later_services_are_not_asked():
    a, b = Fake("A", answer="from A"), Fake("B", answer="from B")
    assert cloud.FallbackChain([a, b]).generate("q") == "from A"
    assert b.asked == []


def test_when_every_service_fails_the_error_says_why_for_each():
    chain = cloud.FallbackChain([Fake("A", error="key refused"),
                                 Fake("B", error="timed out")])
    with pytest.raises(cloud.CloudAIError, match="A: key refused; B: timed out"):
        chain.generate("q")


def test_offsite_if_any_member_is():
    local = Fake("local Ollama", offsite=False)
    assert cloud.FallbackChain([local, Fake("cloud")]).sends_offsite is True
    assert cloud.FallbackChain([local, Fake("other", offsite=False)]).sends_offsite \
        is False


# --- the report -------------------------------------------------------------------

def _service(tmp_path, chain):
    from ai.service import AnalysisService
    repo = Repository(str(tmp_path / "v.db"))
    repo.ensure_vehicle(VIN, make="VW", model="ID.3")
    svc = AnalysisService(_cfg(), repo)
    svc.llm = chain
    return svc, repo


def test_a_backup_report_says_so_and_records_its_model(tmp_path):
    answer = "All fine.\n\nLIMITATIONS\n- synthetic"
    chain = cloud.FallbackChain([Fake("Google Gemini", "gemini-x",
                                      error="rate limit reached"),
                                 Fake("Groq", "llama-x", answer=answer)])
    svc, repo = _service(tmp_path, chain)
    result = svc.ask("How is the battery?")
    assert result["model"] == "llama-x"
    assert result["warnings"][0] == ("Written by the backup Groq (llama-x): "
                                     "Google Gemini failed (rate limit reached)")
    stored = repo.conn.execute("SELECT model FROM llm_reports").fetchone()
    assert stored["model"] == "llama-x"
    repo.close()


def test_the_vin_is_removed_when_ollama_comes_first_but_a_cloud_follows(tmp_path):
    local = Fake("local Ollama", offsite=False, error="Ollama is not running")
    remote = Fake("Groq", answer="ok\n\nLIMITATIONS\n- x")
    svc, repo = _service(tmp_path, cloud.FallbackChain([local, remote]))
    svc.ask(f"Is {VIN} healthy?")
    assert remote.asked and all(VIN not in p for p in local.asked + remote.asked)
    repo.close()


def test_the_page_names_the_whole_chain(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": str(tmp_path / "p.db")},
                  "ai": {"provider": "ollama", "fallbacks": [
                      {"provider": "groq", "api_key": "gsk-SECRET-1"}]},
                  "ollama": {"host": "http://localhost:11434", "model": "qwen"},
                  "web": {"host": "127.0.0.1"}})
    with TestClient(create_app(cfg, bind_host="127.0.0.1"),
                    raise_server_exceptions=False) as client:
        page = client.get("/ai").text
    assert "via local Ollama, then Groq (free tier) if it fails" in page
    assert "Cloud AI:" in page, "a cloud backup must trigger the cloud warning"
    assert "SECRET" not in page
