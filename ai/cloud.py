"""Cloud AI services for the AI Analysis page, beside the local Ollama.

`ai.provider` picks the service. Claude goes through Anthropic's own SDK
(`anthropic`); the others -- OpenAI, Google Gemini, Mistral, Groq, OpenRouter,
DeepSeek, xAI, or any OpenAI-compatible endpoint -- through the OpenAI-style
chat completions request they all accept. Every client has OllamaClient's
interface (model, generate, available, list_models), so AnalysisService does
not care which one it holds.

What leaves the machine: the question and the evidence summary AnalysisService
builds -- with the VIN removed (`redact`) -- and nothing else. The LLM still
only interprets: it receives numbers, returns text, and can reach nothing.
The key comes from config.local.yaml (ai.api_key) or ODB_AI_KEY, never from
the shared config.yaml.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import requests

from ai.ollama import AIError
from ai.ollama import client_from_config as ollama_client

log = logging.getLogger(__name__)

TRUNCATED_MARKER = "[OUTPUT TRUNCATED: the model hit its output limit]"
CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"
# Server-side fallback: a request Claude's safety classifiers decline is re-run
# on the model Anthropic recommends for that category, in the same call.
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = ("low", "medium", "high", "xhigh", "max")


class CloudAIError(AIError):
    """A cloud AI request failed; the message says why, in plain words."""


@dataclass(frozen=True)
class Preset:
    label: str
    protocol: str          # "anthropic" | "openai" (OpenAI-compatible chat completions)
    base_url: str
    key_url: str           # where to create a key
    default_model: str = ""   # used when ai.model is blank; "" means one must be named


PRESETS: dict[str, Preset] = {
    "anthropic": Preset("Claude (Anthropic)", "anthropic", "https://api.anthropic.com",
                        "https://console.anthropic.com/settings/keys"),
    "openai": Preset("OpenAI", "openai", "https://api.openai.com/v1",
                     "https://platform.openai.com/api-keys"),
    # Free tiers (checked October 2026): Gemini's needs no card; Groq's has daily
    # limits; Mistral's Experiment plan needs a verified phone number. Gemini's
    # and Mistral's free tiers may use what is sent to improve their models.
    # Groq shut llama-3.3-70b-versatile down on 2026-08-16; gpt-oss-120b is its
    # listed replacement (console.groq.com/docs/deprecations) and calls tools.
    "gemini": Preset("Google Gemini (free tier)", "openai",
                     "https://generativelanguage.googleapis.com/v1beta/openai",
                     "https://aistudio.google.com/apikey", "gemini-3.8-flash"),
    "mistral": Preset("Mistral (free Experiment plan)", "openai",
                      "https://api.mistral.ai/v1",
                      "https://console.mistral.ai/api-keys", "mistral-small-latest"),
    "groq": Preset("Groq (free tier)", "openai", "https://api.groq.com/openai/v1",
                   "https://console.groq.com/keys", "openai/gpt-oss-120b"),
    "openrouter": Preset("OpenRouter (many models, one key)", "openai",
                         "https://openrouter.ai/api/v1", "https://openrouter.ai/keys"),
    "deepseek": Preset("DeepSeek", "openai", "https://api.deepseek.com/v1",
                       "https://platform.deepseek.com/api_keys"),
    "xai": Preset("xAI Grok", "openai", "https://api.x.ai/v1", "https://console.x.ai"),
    "custom": Preset("Other OpenAI-compatible service", "openai", "", ""),
}

_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")


def redact(value, vin: str | None = None):
    """A copy of `value` with every VIN removed: `vin` keys dropped and any
    17-character VIN-shaped string (or the known `vin`) replaced."""
    if isinstance(value, dict):
        return {k: redact(v, vin) for k, v in value.items() if str(k).lower() != "vin"}
    if isinstance(value, (list, tuple)):
        return [redact(v, vin) for v in value]
    if isinstance(value, str):
        if vin:
            value = value.replace(vin, "[VIN]")
        return _VIN.sub("[VIN]", value)
    return value


class _Cloud:
    sends_offsite = True

    def __init__(self, preset_id: str, preset: Preset, api_key: str, model: str,
                 timeout: float):
        self.preset_id = preset_id
        self.preset = preset
        self.key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def where(self) -> str:
        return self.preset.label

    def _need_key(self):
        if not self.key:
            raise CloudAIError(
                f"No API key for {self.preset.label}. Create one at "
                f"{self.preset.key_url or 'the service'} and set ai.api_key in "
                "config.local.yaml (or ODB_AI_KEY).")


class AnthropicClient(_Cloud):
    """Claude through the official SDK: beta Messages API with the server-side
    refusal fallback, effort set explicitly, no sampling parameters."""

    def __init__(self, api_key: str, model: str = "", effort: str = "medium",
                 timeout: float = 300.0, max_tokens: int = 16000):
        super().__init__("anthropic", PRESETS["anthropic"], api_key,
                         model or CLAUDE_DEFAULT_MODEL, timeout)
        self.effort = effort if effort in EFFORTS else "medium"
        self.max_tokens = int(max_tokens)

    def _client(self):
        try:
            import anthropic
        except ImportError as exc:
            raise CloudAIError("The Claude option needs the anthropic package: "
                               "python -m pip install anthropic") from exc
        return anthropic, anthropic.Anthropic(api_key=self.key, timeout=self.timeout)

    def available(self) -> bool:
        return bool(self.key)

    def list_models(self) -> list[str]:
        self._need_key()
        anthropic, client = self._client()
        try:
            return [m.id for m in client.models.list()]
        except anthropic.APIError as exc:
            raise CloudAIError(f"Cannot list Claude models: {exc}") from exc

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float | None = None) -> str:
        """`temperature` is accepted for interface parity and not sent: current
        Claude models reject sampling parameters."""
        self._need_key()
        anthropic, client = self._client()
        kwargs = dict(
            model=self.model, max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort},
            betas=[CLAUDE_FALLBACK_BETA], fallbacks="default",
        )
        if system:
            kwargs["system"] = system
        try:
            response = client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise CloudAIError("Anthropic refused the API key (401). Check ai.api_key "
                               "in config.local.yaml.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise CloudAIError(f"The API key may not use {self.model} (403).") from exc
        except anthropic.NotFoundError as exc:
            raise CloudAIError(f"Claude model '{self.model}' not found (404). "
                               "Set ai.model to one your key can use.") from exc
        except anthropic.RateLimitError as exc:
            raise CloudAIError("Anthropic rate limit reached (429); try again "
                               "in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise CloudAIError(f"Anthropic returned HTTP {exc.status_code}: "
                               f"{exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise CloudAIError(f"Could not reach Anthropic: {exc}") from exc
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) \
                if response.stop_details else None
            raise CloudAIError("Claude declined this request"
                               + (f" ({category})" if category else "")
                               + "; rephrase the question or try another model.")
        # Read blocks by type: thinking and fallback-marker blocks carry no text.
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise CloudAIError("Claude returned no text.")
        if response.stop_reason == "max_tokens":
            text += "\n\n" + TRUNCATED_MARKER
        return text


class OpenAICompatibleClient(_Cloud):
    """Any service that speaks OpenAI's chat completions: one request shape.
    Neither temperature nor an output cap is sent -- several newer models
    reject non-default values -- so the service's defaults apply."""

    def __init__(self, preset_id: str, api_key: str, model: str, base_url: str = "",
                 timeout: float = 300.0):
        preset = PRESETS[preset_id]
        super().__init__(preset_id, preset, api_key, model, timeout)
        self.base_url = (base_url or preset.base_url).rstrip("/")
        if not self.base_url:
            raise CloudAIError("ai.provider custom needs ai.base_url, e.g. "
                               "https://example.com/v1")

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def _check(self, r, what: str):
        host = self.base_url.split("/")[2] if "//" in self.base_url else self.base_url
        if r.status_code in (401, 403):
            raise CloudAIError(f"{host} refused the API key (HTTP {r.status_code}). "
                               "Check ai.api_key in config.local.yaml.")
        if r.status_code == 404:
            raise CloudAIError(f"{host}: {what} not found (404) -- check ai.model.")
        if r.status_code == 429:
            raise CloudAIError(f"{host} rate limit reached (429); try again later.")
        if r.status_code >= 400:
            raise CloudAIError(f"{host} returned HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError as exc:
            raise CloudAIError(f"{host} returned a non-JSON reply: "
                               f"{r.text[:200]!r}") from exc

    def available(self) -> bool:
        return bool(self.key) or self.preset_id == "custom"

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(),
                             timeout=min(self.timeout, 30))
        except requests.RequestException as exc:
            raise CloudAIError(f"Cannot list models: {exc}") from exc
        data = self._check(r, "model list")
        rows = data.get("data", []) if isinstance(data, dict) else []
        return sorted(str(m.get("id")) for m in rows
                      if isinstance(m, dict) and m.get("id"))

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float | None = None) -> str:
        self._need_key()
        if not self.model:
            try:
                some = ", ".join(self.list_models()[:15])
            except CloudAIError:
                some = ""
            raise CloudAIError(f"Set ai.model for {self.preset.label}"
                               + (f"; it offers: {some}" if some else "."))
        messages = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user", "content": prompt}]
        try:
            r = requests.post(f"{self.base_url}/chat/completions",
                              headers=self._headers(),
                              json={"model": self.model, "messages": messages},
                              timeout=self.timeout)
        except requests.RequestException as exc:
            raise CloudAIError(f"Could not reach {self.preset.label}: {exc}") from exc
        data = self._check(r, f"model '{self.model}'")
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise CloudAIError(f"Unexpected reply from {self.preset.label}: "
                               f"{str(data)[:200]}") from exc
        if isinstance(content, list):          # content parts, as some services return
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = (content or "").strip()
        if not text:
            raise CloudAIError(f"{self.preset.label} returned no text.")
        if choice.get("finish_reason") == "length":
            text += "\n\n" + TRUNCATED_MARKER
        return text


class FallbackChain:
    """The configured AI, then each of ai.fallbacks in turn when one fails.

    A free tier's rate limit, an outage, a refused key or a local Ollama that
    is not running all surface as AIError; the same question then goes to the
    next service. Same interface as the clients, so AnalysisService holds it
    like any other. Two things follow from being several services at once:

    * sends_offsite is true if ANY member sends off the machine -- a chain
      that starts with the local Ollama can still fall back to a cloud
      service, so the VIN is removed before anything is asked.
    * Which member answered is returned with the answer (generate_with_source)
      rather than kept as state, so a report records the model that wrote it.
    """

    def __init__(self, members: list):
        if not members:
            raise CloudAIError("No AI service configured.")
        self.members = members

    @property
    def model(self) -> str:
        return self.members[0].model

    @property
    def where(self) -> str:
        names = [m.where for m in self.members]
        return names[0] + "".join(f", then {n}" for n in names[1:]) + \
            (" if it fails" if len(names) == 2 else " as each fails")

    @property
    def sends_offsite(self) -> bool:
        return any(getattr(m, "sends_offsite", False) for m in self.members)

    def available(self) -> bool:
        for m in self.members:
            try:
                if m.available():
                    return True
            except AIError:
                continue
        return False

    def list_models(self) -> list[str]:
        return self.members[0].list_models()

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float | None = None) -> str:
        return self.generate_with_source(prompt, system, temperature)[0]

    def generate_with_source(self, prompt: str, system: str | None = None,
                             temperature: float | None = None):
        """(text, the member that wrote it, [(where, why) for each that failed])."""
        failed: list[tuple[str, str]] = []
        for member in self.members:
            try:
                text = member.generate(prompt, system=system, temperature=temperature)
            except AIError as exc:
                log.warning("AI %s failed (%s); trying the next", member.where, exc)
                failed.append((member.where, str(exc)))
                continue
            return text, member, failed
        raise CloudAIError("Every AI service failed: " + "; ".join(
            f"{where}: {why}" for where, why in failed))


def _client(provider: str, key: str, model: str, base_url: str, effort: str,
            timeout: float, cfg):
    provider = (provider or "ollama").strip().lower()
    if provider == "ollama":
        return ollama_client(cfg)
    if provider not in PRESETS:
        raise CloudAIError(f"Unknown ai.provider '{provider}'. Use ollama or one of: "
                           + ", ".join(PRESETS))
    model = model.strip() or PRESETS[provider].default_model
    if PRESETS[provider].protocol == "anthropic":
        return AnthropicClient(key.strip(), model, effort, timeout)
    return OpenAICompatibleClient(provider, key.strip(), model, base_url, timeout)


def llm_from_config(cfg):
    """The configured AI client: the local Ollama unless ai.provider names a
    cloud service -- with ai.fallbacks behind it, a FallbackChain.

    Each fallback is a provider name, or a mapping with its own api_key (and
    optionally model, base_url, effort); its key comes from config.local.yaml
    like ai.api_key. A fallback that cannot be built is logged and left out,
    so a mistake in a backup never takes the main service down with it.
    """
    timeout = float(cfg.get("ai.timeout", 300) or 300)
    primary = _client(
        str(cfg.get("ai.provider", "ollama") or "ollama"),
        str(cfg.get("ai.api_key", "") or os.environ.get("ODB_AI_KEY", "")),
        str(cfg.get("ai.model", "") or ""), str(cfg.get("ai.base_url", "") or ""),
        str(cfg.get("ai.effort", "medium")), timeout, cfg)
    backups = []
    for entry in cfg.get("ai.fallbacks") or []:
        spec = {"provider": entry} if isinstance(entry, str) else entry
        if not isinstance(spec, dict) or not spec.get("provider"):
            log.warning("ai.fallbacks: %r is not a provider name or a mapping "
                        "with one; left out", entry)
            continue
        try:
            backups.append(_client(
                str(spec["provider"]), str(spec.get("api_key", "") or ""),
                str(spec.get("model", "") or ""), str(spec.get("base_url", "") or ""),
                str(spec.get("effort", "medium")),
                float(spec.get("timeout", timeout) or timeout), cfg))
        except AIError as exc:
            log.warning("ai.fallbacks: %s left out: %s", spec.get("provider"), exc)
    return FallbackChain([primary] + backups) if backups else primary
