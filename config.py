"""Configuration loading (config.yaml + environment overrides)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent / "config.yaml"


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


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_PATH
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {}
    return Config(data)
