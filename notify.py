"""Tell the owner when something needs them: the log, a phone push, the desktop.

Alerts are for things worth interrupting someone for -- a new fault code, the
12 V battery low while the car is parked, a backup that failed its check, a
filling disk, a finished charge. Every alert is logged at WARNING. Configured
under `notify:`, it is also pushed to ntfy (ntfy.sh or a self-hosted server;
the phone app shows it as a notification) and shown on the desktop.

The ntfy topic URL belongs in config.local.yaml: anyone who knows a topic can
read it, so it is a secret in all but name.

Sending never raises. An alert is a side effect of collecting, backing up or
analysing, and a push service that is down must not take any of those with
it -- the log line is always written first.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

PRIORITIES = ("min", "low", "default", "high", "urgent")


def _one_line(text: str, limit: int) -> str:
    """No CR/LF (ntfy takes the title as an HTTP header), bounded length."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


class Notifier:
    """Send alerts through the configured channels; see the module docstring."""

    def __init__(self, cfg):
        self.ntfy_url = str(cfg.get("notify.ntfy_url") or "").strip()
        self.ntfy_token = str(cfg.get("notify.ntfy_token") or "").strip()
        self.desktop = bool(cfg.get("notify.desktop", False))
        self._last_sent: dict[str, float] = {}

    def send(self, title: str, message: str, *, priority: str = "default",
             key: str | None = None, cooldown_s: float = 6 * 3600) -> bool:
        """Alert, unless `key` was alerted less than `cooldown_s` ago.

        Returns whether the alert went out (it is always logged when it does).
        The cooldown is per process: a collector repeating the same condition
        every cycle alerts once, not every five seconds.
        """
        if key is not None:
            now = time.monotonic()
            last = self._last_sent.get(key)
            if last is not None and now - last < cooldown_s:
                return False
            self._last_sent[key] = now
        title = _one_line(title, 120)
        message = str(message).strip()
        log.warning("ALERT %s: %s", title, message)
        if self.ntfy_url:
            self._ntfy(title, message,
                       priority if priority in PRIORITIES else "default")
        if self.desktop:
            self._desktop(title, message, priority)
        return True

    def _ntfy(self, title: str, message: str, priority: str) -> None:
        import requests
        headers = {"Title": title, "Priority": priority, "Tags": "car"}
        if self.ntfy_token:
            headers["Authorization"] = f"Bearer {self.ntfy_token}"
        try:
            response = requests.post(self.ntfy_url,
                                     data=message.encode("utf-8"),
                                     headers=headers, timeout=10)
            if response.status_code >= 400:
                log.warning("ntfy refused the alert: HTTP %s",
                            response.status_code)
        except Exception as exc:  # noqa: BLE001 - an alert must never raise
            # Not the URL: it carries the topic.
            log.warning("could not reach ntfy: %s", type(exc).__name__)

    def _desktop(self, title: str, message: str, priority: str) -> None:
        exe = shutil.which("notify-send")
        if exe is None:
            log.debug("notify.desktop is on but notify-send is not installed")
            return
        urgency = {"high": "critical", "urgent": "critical",
                   "min": "low", "low": "low"}.get(priority, "normal")
        try:
            subprocess.run([exe, "--app-name=odb_scanner", f"--urgency={urgency}",
                            title, message], timeout=5, check=False,
                           capture_output=True)
        except Exception as exc:  # noqa: BLE001 - an alert must never raise
            log.debug("notify-send failed: %s", exc)
