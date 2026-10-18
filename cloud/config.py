"""Settings for the two cloud halves.

They come from different places on purpose:

  * The COLLECTOR's settings (API URL, token, batch size) live in
    config.yaml / config.local.yaml, exactly like the rest of the app. The one
    secret among them -- the API token -- follows the established pattern of
    environment first, then a gitignored local file.

  * The API SERVICE's settings (the MySQL connection) come from the
    environment only. It runs as its own container in the cloud, where there is
    no config.yaml to read, and a deployed credential belongs in the
    environment rather than in a file that can be baked into an image.

Nothing here imports a database driver or an HTTP library, so it is safe to
import from tests and from the collector on any machine.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass


class CloudConfigError(ValueError):
    """Cloud logging was switched on but is not usable as configured."""


def _as_bool(value, default: bool = False) -> bool:
    """YAML gives real bools; env vars and hand-edited files give strings.

    A bare ``if value`` would treat "false" as true, which for `enabled` means
    quietly shipping every reading to a server the operator believes is off.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ClientSettings:
    enabled: bool
    api_url: str
    token: str
    source_id: str
    batch_size: int
    sync_interval_s: float
    timeout_s: float
    verify_tls: bool

    @property
    def upload_url(self) -> str:
        return self.api_url.rstrip("/") + "/ingest/measurements"


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    ssl_ca: str = ""          # path to a CA bundle; empty = no TLS


def default_source_id(hostname: str | None = None) -> str:
    """A stable per-host identity for the cloud `sources` table.

    Hostname-based so an operator recognises it; the collector's own id only
    needs to be stable across restarts, not globally unique, because the API
    keys uploads by measurement UUID rather than by source.
    """
    name = (hostname or socket.gethostname() or "unknown").strip().lower()
    # Keep it inside the schema's VARCHAR(120) and free of whitespace that
    # would look like two different sources.
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)
    return f"python:{safe[:100]}"


def client_settings(cfg) -> ClientSettings:
    """Build the collector-side cloud settings from a loaded Config.

    Enabled state, URL and token may each be overridden by the environment
    (ODB_CLOUD_ENABLED / ODB_CLOUD_API_URL / ODB_CLOUD_TOKEN, the last via
    Config.cloud_token). That keeps secrets and per-run targets out of the
    committed file, and lets the local end-to-end check point the collector at
    a throwaway stack without editing config.local.yaml.

    Raises ``CloudConfigError`` when enabled but incomplete, rather than
    returning a half-configured object that fails later on the first upload
    with a confusing network error. A disabled cloud is always valid.
    """
    raw_enabled = os.environ.get("ODB_CLOUD_ENABLED")
    enabled = _as_bool(raw_enabled if raw_enabled is not None
                       else cfg.get("cloud.enabled", False))
    api_url = (os.environ.get("ODB_CLOUD_API_URL")
               or cfg.get("cloud.api_url", "") or "").strip()
    token = str(getattr(cfg, "cloud_token", "") or "").strip()
    source_id = (os.environ.get("ODB_CLOUD_SOURCE_ID")
                 or cfg.get("cloud.source_id", "") or "").strip() \
        or default_source_id()

    if enabled:
        if not api_url:
            raise CloudConfigError(
                "cloud.enabled is true but cloud.api_url is empty. Set it to "
                "the ingest API base URL (see cloud/README.md), or set "
                "cloud.enabled to false.")
        if not api_url.startswith(("http://", "https://")):
            raise CloudConfigError(
                f"cloud.api_url must start with http:// or https://, got "
                f"{api_url!r}")
        if not token:
            raise CloudConfigError(
                "cloud.enabled is true but no API token is set. Put "
                "`cloud.token` in config.local.yaml (gitignored) or export "
                "ODB_CLOUD_TOKEN.")

    return ClientSettings(
        enabled=enabled,
        api_url=api_url,
        token=token,
        source_id=source_id,
        batch_size=int(cfg.get("cloud.batch_size", 500)),
        sync_interval_s=float(cfg.get("cloud.sync_interval_s", 60.0)),
        timeout_s=float(cfg.get("cloud.timeout_s", 15.0)),
        verify_tls=_as_bool(cfg.get("cloud.verify_tls", True), True),
    )


def db_settings_from_env(env: dict | None = None) -> DbSettings:
    """Connection details for the ingest API, read from the environment.

    Unprefixed names would collide with any other service in the same
    container, so every key is namespaced under ODB_CLOUD_DB_.
    """
    env = os.environ if env is None else env
    missing = [k for k in ("ODB_CLOUD_DB_HOST", "ODB_CLOUD_DB_USER",
                           "ODB_CLOUD_DB_NAME") if not env.get(k)]
    if missing:
        raise CloudConfigError(
            "The ingest API needs its database connection in the environment. "
            f"Missing: {', '.join(missing)}.")
    return DbSettings(
        host=env["ODB_CLOUD_DB_HOST"],
        port=int(env.get("ODB_CLOUD_DB_PORT", 3306)),
        user=env["ODB_CLOUD_DB_USER"],
        password=env.get("ODB_CLOUD_DB_PASSWORD", ""),
        database=env["ODB_CLOUD_DB_NAME"],
        ssl_ca=env.get("ODB_CLOUD_DB_SSL_CA", ""),
    )
