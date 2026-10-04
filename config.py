"""Configuration loading (config.yaml + environment overrides)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent / "config.yaml"
LOCAL_PATH = Path(__file__).resolve().parent / "config.local.yaml"


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base, returning a new dict.

    Nested sections merge key-by-key so a local file can override just
    ``adapter.port`` without having to restate the whole adapter section.
    """
    merged = dict(base)
    for key, value in (overlay or {}).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


class Config:
    """Attribute-style access with defaults; env vars override yaml."""

    def __init__(self, data: dict):
        self._data = data or {}

    def __getattr__(self, name):  # top-level sections and scalar keys
        try:
            value = self._data[name]
        except KeyError:
            raise AttributeError(name) from None
        if isinstance(value, dict):
            return Config(value)
        return value

    def get(self, dotted: str, default=None):
        node: object = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- env overrides (requirement #10: model configurable) ---------------
    @property
    def ollama_host(self) -> str:
        return os.environ.get("OLLAMA_HOST",
                              self.get("ollama.host", "http://localhost:11434"))

    @property
    def ollama_model(self) -> str:
        return os.environ.get("OLLAMA_MODEL",
                              self.get("ollama.model", "qwen2.5:7b"))

    @property
    def ollama_timeout(self) -> float:
        return float(os.environ.get("OLLAMA_TIMEOUT",
                                    self.get("ollama.timeout", 300)))

    @property
    def db_path(self) -> str:
        return self.get("database.path", "data/odb_scanner.db")


class ConfigError(ValueError):
    """Raised when a config file is structurally wrong or a value is unusable."""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    # A file holding a list or a bare scalar is not a config. Left unchecked,
    # merging or lookup would fail much later with an AttributeError or a
    # confusing KeyError that names no file.
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path.name}: expected a mapping of settings, got "
            f"{type(data).__name__}")
    return data


# Values that must be positive numbers. Zero or negative intervals are not
# caught by the code that reads them: a zero poll interval spins the collector
# loop at full speed hammering the adapter, and a negative one silently runs
# backwards. Both are far clearer as a startup error naming the key.
_REQUIRED_POSITIVE = (
    "collector.poll_interval",
    "collector.slow_poll_interval",
    "collector.max_value_age_s",
    "ollama.timeout",
)


def _validate(data: dict) -> None:
    cfg = Config(data)
    for key in _REQUIRED_POSITIVE:
        raw = cfg.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{key}: expected a number, got {raw!r}") from None
        if value <= 0:
            raise ConfigError(f"{key}: must be greater than 0, got {value}")
    slow = cfg.get("collector.slow_poll_interval", 60.0)
    age = cfg.get("collector.max_value_age_s", 900.0)
    if float(age) <= float(slow):
        # Not fatal on its own, but the carry-forward window for slow DIDs is
        # then shorter than the interval at which they are read, so every slow
        # value would be missing from most snapshots and recorded as unknown --
        # which looks like a sensor fault rather than a setting.
        raise ConfigError(
            "collector.max_value_age_s must exceed "
            "collector.slow_poll_interval, otherwise values that are "
            f"legitimately absent from a cycle are dropped ({age} <= {slow})")


def load_config(path: str | Path | None = None,
                local_path: str | Path | None = None) -> Config:
    """Load config.yaml, then deep-merge config.local.yaml over it if present.

    config.local.yaml is gitignored and optional -- use it for personal
    overrides (COM port, model name, adapter host) so config.yaml stays
    shareable. Missing files are not an error; an unreadable or malformed one
    is, because silently ignoring it would be worse than failing to start.
    """
    base_path = Path(path) if path else DEFAULT_PATH
    if local_path is not None:
        overlay_path = Path(local_path)
    elif base_path == DEFAULT_PATH:
        overlay_path = LOCAL_PATH
    else:
        overlay_path = base_path.with_name(
            base_path.stem.replace(".yaml", "") + ".local.yaml")

    data = _read_yaml(base_path)
    if overlay_path != base_path:
        data = _deep_merge(data, _read_yaml(overlay_path))
    _validate(data)
    return Config(data)
