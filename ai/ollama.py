"""Ollama client — talks to the local Ollama HTTP API.

Model is NOT hard-coded: it comes from config (OLLAMA_MODEL / config.yaml).
No vehicle commands are ever routed through the LLM; it receives structured
JSON evidence and returns text only.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class AIError(RuntimeError):
    """Any AI backend failure (local or cloud), with a message fit to show."""


class OllamaError(AIError):
    pass


# Ollama silently truncates a prompt longer than its context window (the
# default is 2048 or 4096 tokens depending on version), so the evidence or the
# analyst rules could drop out with no error -- the real evidence packet is
# already ~12k characters. And with no output cap, a runaway generation ran
# to the HTTP timeout and the whole report was lost. Both are explicit now.
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 1024

TRUNCATED_MARKER = "[OUTPUT TRUNCATED: the model hit the num_predict limit]"

AVAILABLE_TTL_S = 30.0


class OllamaClient:
    sends_offsite = False            # runs on this machine (or the LAN host set)
    where = "local Ollama"

    def __init__(self, host: str, model: str, timeout: float = 120.0,
                 num_ctx: int = DEFAULT_NUM_CTX,
                 num_predict: int = DEFAULT_NUM_PREDICT):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_ctx = int(num_ctx)
        self.num_predict = int(num_predict)
        self._seen: tuple[float, bool] | None = None

    def available(self) -> bool:
        """Whether Ollama answers, remembered for AVAILABLE_TTL_S.

        When Ollama is not running, Windows takes about 2 s per address of
        "localhost" (::1, then 127.0.0.1) to refuse the connection, so an
        unremembered probe made every /api/health call take 4 s.
        """
        now = time.monotonic()
        if self._seen is not None and now - self._seen[0] < AVAILABLE_TTL_S:
            return self._seen[1]
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            ok = r.status_code == 200
        except requests.RequestException:
            ok = False
        self._seen = (now, ok)
        return ok

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except (requests.RequestException, ValueError) as exc:
            raise OllamaError(f"Cannot list Ollama models: {exc}") from exc

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature,
                        "num_ctx": self.num_ctx,
                        "num_predict": self.num_predict},
        }
        if system:
            payload["system"] = system
        try:
            r = requests.post(f"{self.host}/api/generate", json=payload,
                              timeout=self.timeout)
        except requests.RequestException as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        if r.status_code == 404:
            raise OllamaError(
                f"Model '{self.model}' not found on {self.host}. "
                f"Run: ollama pull {self.model} (available: "
                f"{self.list_models()})"
            )
        # Everything below must surface as OllamaError: callers (the /ai page,
        # `main.py report`) handle that and nothing else, so a model-runner
        # crash (HTTP 500) or a proxy's HTML page used to become a 500 page or
        # a traceback instead of an explained failure.
        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            raise OllamaError(f"Ollama returned HTTP {r.status_code}: "
                              f"{r.text[:300]}") from exc
        try:
            data = r.json()
        except ValueError as exc:
            raise OllamaError(f"Ollama returned a non-JSON response: "
                              f"{r.text[:300]!r}") from exc
        if not isinstance(data, dict):
            raise OllamaError(f"Unexpected response from Ollama: {data!r}")
        text = data.get("response", "")
        if not text:
            raise OllamaError(f"Empty response from Ollama: {data!r}")
        if data.get("done_reason") == "length":
            log.warning("Ollama stopped at num_predict=%d; report truncated",
                        self.num_predict)
            text = text.rstrip() + "\n\n" + TRUNCATED_MARKER
        return text


def client_from_config(cfg) -> OllamaClient:
    return OllamaClient(
        cfg.ollama_host, cfg.ollama_model, cfg.ollama_timeout,
        num_ctx=int(cfg.get("ollama.num_ctx", DEFAULT_NUM_CTX)),
        num_predict=int(cfg.get("ollama.num_predict", DEFAULT_NUM_PREDICT)))
