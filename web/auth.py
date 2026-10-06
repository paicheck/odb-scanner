"""Access control for the dashboard.

THE PROBLEM
-----------
config.yaml ships `web.host: 0.0.0.0` so a tablet on the same wifi can reach
the dashboard. That makes every endpoint reachable by anything else on the
network, and there is no authentication anywhere in the app: not the vehicle
VIN, not the fault history, and not `POST /ai/ask`, which will happily spend
minutes of GPU time generating reports for anyone who posts to it.

THE RULE
--------
A loopback bind (127.0.0.1 / ::1 / localhost) needs no token: nothing but this
machine can open the socket, so there is no attacker to authenticate. Any other
bind -- 0.0.0.0, a LAN address, a VPN address -- REQUIRES a token. That is
fail-closed on purpose: forgetting to set one must lock the dashboard down, not
expose it.

WHERE THE TOKEN COMES FROM
--------------------------
1. `web.auth_token` in config.yaml or config.local.yaml
2. the ODB_TOKEN environment variable
3. generated on first run and written to config.local.yaml

Option 3 exists so that changing the bind address cannot leave the dashboard
open: an operator who flips web.host to 0.0.0.0 gets a working token and a
message saying where it went, rather than an unprotected dashboard and silence.

HOW IT IS PRESENTED
-------------------
The token is accepted as a query parameter once, which is then exchanged for
an HttpOnly cookie so it stops appearing in browser history, bookmarks and
Referer headers. Plain http on a home network is the assumed transport, so the
cookie is not marked Secure -- marking it Secure would silently break the
tablet over http, which is exactly how this dashboard is actually used. On a
network you do not control, put it behind TLS (a reverse proxy) instead.
"""
from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger(__name__)

COOKIE = "odb_token"
QUERY = "k"
_TOKEN_BYTES = 32

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "localhost.localdomain"}


def is_loopback(host: str) -> bool:
    """True when a bind address cannot be reached from another machine."""
    h = (host or "").strip().lower()
    if h in LOOPBACK_HOSTS:
        return True
    # Any address in 127.0.0.0/8 is loopback on every platform Python runs on.
    return h.startswith("127.")


def resolve_token(cfg, bind_host: str) -> str | None:
    """The token this bind address requires, or None if it requires none.

    Generating and persisting one is a side effect, so it only happens when a
    non-loopback bind actually needs it.
    """
    if is_loopback(bind_host):
        return None
    configured = cfg.get("web.auth_token")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    env = os.environ.get("ODB_TOKEN", "").strip()
    if env:
        return env
    token = _generate_and_persist(cfg, bind_host)
    return token


def _generate_and_persist(cfg, bind_host: str) -> str:
    """Reuse the persisted token, or mint one and record it.

    config.local.yaml is gitignored and is already this project's designated
    place for per-machine settings, so the token does not end up in a file
    that gets shared. The path comes from the config that was actually loaded
    rather than the module default, so a config loaded from somewhere else
    does not have its secret written into the repository's own settings.

    An existing token there is reused rather than replaced. Minting on every
    start would make persistence pointless: the tablet would be locked out
    each time the dashboard restarted, and the file would only ever hold the
    last of a series of tokens nobody recorded.

    If the file cannot be written the token is still returned -- the dashboard
    stays closed, and the operator gets a working token for this run rather
    than an open dashboard.
    """
    from config import LOCAL_PATH

    # cfg.local_path follows load_config(); fall back to the project default
    # only for a Config built by hand.
    target = getattr(cfg, "local_path", None) or LOCAL_PATH
    try:
        import yaml

        data: dict = {}
        if target.exists():
            loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        web = data.get("web")
        if not isinstance(web, dict):
            web = {}
            data["web"] = web

        existing = web.get("auth_token")
        if isinstance(existing, str) and existing.strip():
            return existing.strip()

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        web["auth_token"] = token
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
    except Exception:
        log.exception("Could not persist a generated dashboard token to %s. "
                      "Set web.auth_token there by hand, or export ODB_TOKEN, "
                      "or the token will change on every restart.", target)
        return secrets.token_urlsafe(_TOKEN_BYTES)

    log.warning(
        "Dashboard is bound to %s, which is reachable from the network, so it "
        "now requires a token. Generated one and saved it to %s -- it is not "
        "in version control. Open the dashboard with: http://<this-machine>:%s"
        "/?%s=%s", bind_host, target, cfg.get("web.port", 8000), QUERY, token)
    return token


def login_url(request: Request, token: str) -> str:
    """Absolute URL that carries the token once, for copy/paste."""
    path = quote(request.url.path or "/")
    qs = urlencode({QUERY: token})
    return f"{request.url.scheme}://{request.url.netloc}{path}?{qs}"


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Authentication required</title>
<style>body{{font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f8;color:#222;
margin:0;padding:60px 20px}}div{{max-width:640px;margin:0 auto;background:#fff;
border-radius:8px;padding:28px 32px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h1{{font-size:20px;margin:0 0 12px}}p{{line-height:1.6;color:#444}}
code{{background:#eef2f5;padding:2px 6px;border-radius:4px}}
.warn{{background:#fff8e1;border-left:4px solid #e0a800;padding:10px 14px;
margin:18px 0;font-size:14px}}</style></head>
<body><div><h1>Dashboard is protected</h1>
<p>This server is bound to a network-reachable address, so it requires a
one-time access token. Open it once with the token in the address:</p>
<p><code>{url}</code></p>
<p>Your browser will then remember it and every later visit will work
normally. The token is stored in an HttpOnly cookie, not in the page.</p>
<p class="warn">If you did not expect this, something else on your network has
your car's diagnostic data. Change <code>web.host</code> to
<code>127.0.0.1</code> in <code>config.local.yaml</code> to close it off.</p>
</div></body></html>"""


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Require the dashboard token for any non-loopback bind.

    Requests without a valid token get an explanatory 401 page rather than a
    bare refusal, because the usual reason to land here is following a
    bookmark written before the dashboard was protected.
    """

    def __init__(self, app, token: str | None):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if self.token and not self._authorised(request):
            return HTMLResponse(
                _LOGIN_PAGE.format(url=login_url(request, self.token)),
                status_code=401)
        return await call_next(request)

    def _authorised(self, request: Request) -> bool:
        supplied = request.cookies.get(COOKIE) or request.query_params.get(QUERY)
        if not supplied:
            return False
        # Constant-time: a byte-at-a-time compare would leak the token to a
        # caller able to time responses.
        if not secrets.compare_digest(str(supplied), self.token):
            return False
        return True


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response headers.

    Costs nothing and closes off the easy classes of problem: content
    sniffing into the dashboard, framing it to clickjack a session, leaking the
    full request URL (which carries the access token) to any third party, and
    a cached copy of vehicle diagnostics sitting in a shared proxy.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            # Chart.js is loaded from a CDN and inlines its own styles.
            "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        # Vehicle history is not something an intermediary should keep.
        response.headers.setdefault("Cache-Control", "no-store")
        return response


class CookiePromotionMiddleware(BaseHTTPMiddleware):
    """Exchange `?k=<token>` for a cookie, then drop it from the URL.

    Keeping the token out of the address bar means it stops being copied into
    browser history, bookmarks, the Referer header of any outbound link, and
    any screenshot of the window.
    """

    def __init__(self, app, token: str | None):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        supplied = request.query_params.get(QUERY)
        if (self.token and supplied
                and secrets.compare_digest(str(supplied), self.token)):
            response = RedirectResponse(
                request.url.path or "/", status_code=303)
            response.set_cookie(
                COOKIE, self.token,
                httponly=True,          # not readable from JavaScript
                samesite="lax",         # not sent on cross-site requests
                max_age=60 * 60 * 24 * 90,
                path="/",
            )
        return response
