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

    def __init__(self, data: dict, path: Path | None = None,
                 local_path: Path | None = None):
        self._data = data or {}
        # Remember where this config came from. Anything that needs to WRITE a
        # setting back (the dashboard access token, see web/auth.py) must not
        # assume the default locations: a config loaded from elsewhere would
        # otherwise have its token written into the repository's own
        # config.local.yaml, silently sharing one machine's secret with every
        # other configuration that uses the default path.
        self.path = Path(path) if path else None
        self.local_path = Path(local_path) if local_path else None

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
    def cloud_token(self) -> str:
        """Bearer token for the cloud ingest API.

        Environment first, then config, matching how the dashboard token is
        handled: a secret belongs in the environment or a gitignored file, never
        in the committed config.yaml. An empty string means "not configured",
        which callers must treat as fatal when cloud logging is enabled rather
        than silently uploading without authentication.
        """
        return (os.environ.get("ODB_CLOUD_TOKEN")
                or self.get("cloud.token", "") or "")

    @property
    def db_path(self) -> str:
        """Where measurements are read and written.

        ``ODB_DB_PATH`` wins so a launcher can point one run at a different
        file without editing configuration -- that is how
        ``tools/start_scanner.py`` keeps a simulator run away from the car's
        database (see ``simulator_db_path``).
        """
        return (os.environ.get("ODB_DB_PATH")
                or self.get("database.path", "data/odb_scanner.db"))

    @property
    def simulator_db_path(self) -> str:
        """Database for simulator runs. Never the car's (see _validate)."""
        return self.get("simulation.db_path", "data/simulator.db")

    @property
    def seed_db_path(self) -> str:
        """Database for `main.py seed`'s example dataset. Never the car's."""
        return self.get("seed.db_path", "data/seed_demo.db")


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
    # Cloud upload pacing. A zero batch size would send empty requests forever
    # and a zero interval would spin the uploader; both are clearer as a
    # startup error naming the key.
    "cloud.batch_size",
    "cloud.sync_interval_s",
    "cloud.timeout_s",
)


def _resolved(path) -> Path:
    try:
        return Path(str(path)).resolve()
    except OSError:
        return Path(str(path))


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

    # Synthetic data must not share a file with measured data. The simulator
    # reports a VIN and publishes plausible engineering values, so once its rows
    # are written next to real ones nothing downstream can tell them apart --
    # which is how a month of simulated readings became part of this vehicle's
    # history. Comparing the two settings here is the earliest point at which
    # the mistake is still cheap to fix.
    for key, label in (("simulation.db_path", "simulator"),
                       ("seed.db_path", "seed")):
        other = cfg.get(key)
        if not other or not cfg.get("database.path"):
            continue
        # Resolved, as main.py cmd_seed does: an absolute database.path and a
        # relative simulation.db_path naming the same file compared unequal as
        # strings and passed.
        if _resolved(other) == _resolved(cfg.get("database.path")):
            raise ConfigError(
                f"{key} and database.path are the same file ({other}). The "
                f"{label} writes synthetic data; pointing it at the car's "
                "database merges the two with no way to tell them apart "
                "afterwards. Give it its own file.")


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
    return Config(data, path=base_path, local_path=overlay_path)
