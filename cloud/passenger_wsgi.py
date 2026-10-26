"""cPanel "Setup Python App" (Passenger) entry point.

Loads secrets from a gitignored `cpanel.env` beside this file (the
`ODB_CLOUD_*` variables that `cloud/config.py` already reads) and builds the
plain-WSGI app from `cloud/wsgi.py`. Create `cpanel.env` from
`cpanel.env.example`. This file itself needs no editing.

The app is built at import time on purpose: a missing token or database
setting fails Passenger's startup visibly instead of the first request.
"""
from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    """Read KEY=VALUE lines into the environment (setdefault: real env wins)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(_HERE / "cpanel.env")

from cloud.wsgi import make_application  # noqa: E402

application = make_application()
