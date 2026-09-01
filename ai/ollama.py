"""Ollama client — talks to the local Ollama HTTP API.

Model is NOT hard-coded: it comes from config (OLLAMA_MODEL / config.yaml).
No vehicle commands are ever routed through the LLM; it receives structured
JSON evidence and returns text only.
"""
from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str, model: str, timeout: float = 120.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

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
            "options": {"temperature": temperature},
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
        r.raise_for_status()
        data = r.json()
        text = data.get("response", "")
        if not text:
            raise OllamaError(f"Empty response from Ollama: {data!r}")
        return text


def client_from_config(cfg) -> OllamaClient:
    return OllamaClient(cfg.ollama_host, cfg.ollama_model,
                        cfg.ollama_timeout)
