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
import re
import secrets
from urllib.parse import urlencode, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
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


_TOKEN_IN_URL = re.compile(rf"([?&]{QUERY}=)[^&\s\"]+")


def redact_token(text: str) -> str:
    """Replace the value of every ?k=... in `text` with <redacted>."""
    return _TOKEN_IN_URL.sub(r"\1<redacted>", text)


class RedactTokenFilter(logging.Filter):
    """Keep the access token out of uvicorn's access log.

    The cookie promotion means a ?k= request is answered with a redirect, but
    the access logger had already written the full request line -- token and
    all -- to data/logs/web.out, so any log handed over for a bug report
    carried the credential.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_token(a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact_token(record.msg)
        return True


def install_access_log_redaction() -> None:
    """Attach RedactTokenFilter to uvicorn's access logger, once."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokenFilter) for f in logger.filters):
        logger.addFilter(RedactTokenFilter())


def masked(token: str) -> str:
    """Enough of the token to recognise it, not enough to use it."""
    return f"{token[:4]}...{token[-2:]}" if len(token) > 8 else "..."


def _origin_of(url: str) -> str:
    """scheme://host:port of a URL, or "" when it has no usable origin."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _safe_path(path: str) -> str:
    """A redirect target that cannot leave this origin.

    '//evil.example/x' is a protocol-relative URL, not a path: handing it to
    RedirectResponse turns the dashboard into an open redirector.
    """
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def _token_matches(supplied: str | None, token: str | None) -> bool:
    """Constant-time comparison that the caller cannot crash.

    secrets.compare_digest refuses str operands holding non-ASCII and raises
    TypeError, so '?k=<any non-ASCII byte>' reached the middleware as an
    unhandled 500 on an unauthenticated path -- one byte to send, a full
    traceback written to the server log every time. Comparing the UTF-8
    encodings keeps the timing property and turns undecodable input into an
    ordinary mismatch.
    """
    if not supplied or not token:
        return False
    try:
        return secrets.compare_digest(str(supplied).encode("utf-8"),
                                      str(token).encode("utf-8"))
    except (AttributeError, UnicodeEncodeError):  # pragma: no cover - defensive
        return False


# This page is served to callers who have NOT authenticated, so it must not
# contain the token. It used to be rendered with the live token interpolated
# into it as a ready-made login link, which meant one unauthenticated GET from
# anywhere on the network returned the credential in the response body.
_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Authentication required</title>
<style>body{font-family:'Segoe UI',Arial,sans-serif;background:#f4f6f8;color:#222;
margin:0;padding:60px 20px}div{max-width:640px;margin:0 auto;background:#fff;
border-radius:8px;padding:28px 32px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{font-size:20px;margin:0 0 12px}p{line-height:1.6;color:#444}
code{background:#eef2f5;padding:2px 6px;border-radius:4px}
.warn{background:#fff8e1;border-left:4px solid #e0a800;padding:10px 14px;
margin:18px 0;font-size:14px}</style></head>
<body><div><h1>Dashboard is protected</h1>
<p>This server is bound to a network-reachable address, so it requires an
access token. Open it once with the token in the address, as
<code>?k=YOUR-TOKEN</code></p>
<p>The token was printed to the console when the dashboard started, and is
stored under <code>web.auth_token</code> in <code>config.local.yaml</code> on
the machine running it. Your browser remembers it afterwards: it is kept in an
HttpOnly cookie and dropped from the address bar on the first visit.</p>
<p class="warn">If you did not expect this page, something else on your network
is asking for your car's diagnostic data. Change <code>web.host</code> to
<code>127.0.0.1</code> in <code>config.local.yaml</code> to close it off.</p>
</div></body></html>"""


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Require the dashboard token for any non-loopback bind.

    Requests without a valid token get an explanatory 401 page rather than a
    bare refusal, because the usual reason to land here is following a
    bookmark written before the dashboard was protected. That page states how
    to supply the token; it must never state the token itself.
    """

    def __init__(self, app, token: str | None):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if self.token and not self._authorised(request):
            return HTMLResponse(_LOGIN_PAGE, status_code=401)
        return await call_next(request)

    def _authorised(self, request: Request) -> bool:
        supplied = request.cookies.get(COOKIE) or request.query_params.get(QUERY)
        # Constant-time: a byte-at-a-time compare would leak the token to a
        # caller able to time responses.
        return _token_matches(supplied, self.token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response headers.

    Costs nothing and closes off the easy classes of problem: content
    sniffing into the dashboard, framing it to clickjack a session, leaking the
    full request URL (which carries the access token) to any third party, and
    a cached copy of vehicle diagnostics sitting in a shared proxy.

    `img_src_extra` adds image origins to img-src -- only the map tile server,
    and only when web.map_tiles enables it. Nothing else is ever loaded from
    outside this origin.
    """

    def __init__(self, app, img_src_extra: str = ""):
        super().__init__(app)
        self.img_src = "'self' data:" + (f" {img_src_extra}" if img_src_extra else "")

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            # All scripts are served from this origin. There is no CDN and no
            # inline script: the previous policy allowed the Chart.js CDN but
            # not inline scripts, so the chart initialisation in every template
            # was silently refused and no chart ever drew.
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src " + self.img_src + "; "
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
        supplied = request.query_params.get(QUERY)
        if self.token and _token_matches(supplied, self.token):
            # Decided BEFORE the handler runs. The old order called the route
            # first and then threw its response away, so a POST carrying ?k=
            # did the whole job for nothing -- a full LLM generation, binned,
            # followed by a 303 to a POST-only path, which the browser then
            # re-requests with GET and gets 405.
            remaining = [(k, v) for k, v in request.query_params.multi_items()
                         if k != QUERY]
            target = _safe_path(request.url.path or "/")
            if remaining:
                # Keep every other parameter: the token hop should not silently
                # drop the rest of the query it was pasted onto.
                target = f"{target}?{urlencode(remaining)}"
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(
                COOKIE, self.token,
                httponly=True,          # not readable from JavaScript
                samesite="lax",         # not sent on cross-site requests
                max_age=60 * 60 * 24 * 90,
                path="/",
            )
            return response
        return await call_next(request)


def _host_name(host_header: str) -> str:
    """Host header without its port, lower case: '[::1]:8000' -> '::1'."""
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    if h.count(":") == 1:
        return h.split(":", 1)[0]
    return h


def _is_ip_literal(name: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def allowed_host_names(cfg, bind_host: str) -> set[str]:
    """Names a browser may legitimately put in Host for this dashboard.

    Loopback names, this machine's hostname (and its .local mDNS form), a bind
    address that is a name rather than 0.0.0.0, and anything in
    web.allowed_hosts (config) or ODB_ALLOWED_HOSTS (comma-separated) -- e.g.
    a reverse proxy's name. IP literals are always accepted separately.
    """
    import socket
    names = {"localhost", "localhost.localdomain"}
    try:
        me = socket.gethostname().strip().lower()
    except OSError:  # pragma: no cover - defensive
        me = ""
    if me:
        names |= {me, f"{me}.local"}
    bind = (bind_host or "").strip().lower()
    if bind and not _is_ip_literal(bind):
        names.add(bind)
    extra = cfg.get("web.allowed_hosts") or []
    if isinstance(extra, str):
        extra = [extra]
    names |= {str(h).strip().lower() for h in extra if str(h).strip()}
    names |= {h.strip().lower() for h in
              os.environ.get("ODB_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return names


class HostAllowListMiddleware(BaseHTTPMiddleware):
    """Refuse requests whose Host header names somewhere else (DNS rebinding).

    A page on attacker.example can re-point attacker.example at 127.0.0.1.
    The browser then treats this dashboard as same-origin with the attacker's
    page: it sends `Host: attacker.example`, SameOriginMiddleware sees Origin
    == the request's own origin, and on a loopback bind -- which needs no token
    -- every page (VIN, faults, history) is readable and POST /ai/ask callable.
    Checking Host closes that. IP literals are always allowed: rebinding needs
    a DNS name whose answer changes, and a browser sending an IP literal as
    Host really did connect to that IP (that is how the tablet reaches a
    network bind). A request with no Host header is not browser-driven.
    """

    def __init__(self, app, allowed: set[str]):
        super().__init__(app)
        self.allowed = {h.lower() for h in allowed}

    async def dispatch(self, request: Request, call_next):
        raw = request.headers.get("host")
        if raw:
            name = _host_name(raw)
            if name not in self.allowed and not _is_ip_literal(name):
                return PlainTextResponse(
                    f"Host {name!r} is not allowed. Add it to web.allowed_hosts "
                    "if this is how you reach the dashboard.", status_code=400)
        return await call_next(request)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class SameOriginMiddleware(BaseHTTPMiddleware):
    """Refuse cross-origin state-changing requests (CSRF).

    POST /ai/ask starts a local LLM generation that occupies the GPU for
    minutes and writes the question into llm_reports, which /ai then renders
    back as trustworthy-looking local history. A form-encoded POST is a
    CORS-simple request, so any page the operator happens to visit can submit
    one with no preflight -- the attacker never reads the response, but the
    write and the resource burn both land, and looping it is a denial of
    service against the machine.

    SameSite=Lax on the cookie does not help here, because on a loopback bind
    there is no token to require at all -- and that is the configuration the
    documentation recommends as the safe one. So this check runs regardless of
    whether a token is configured.

    Browsers send Origin on every cross-origin POST. A request carrying
    neither Origin nor Referer is not browser-driven (curl, a script, a probe)
    and is allowed through: refusing it would break command-line use without
    closing anything a browser is able to do.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() not in _SAFE_METHODS:
            stated = request.headers.get("origin")
            if not stated:
                referer = request.headers.get("referer")
                stated = _origin_of(referer) if referer else ""
            if stated and stated != _origin_of(str(request.url)):
                return PlainTextResponse(
                    "Cross-origin request refused.", status_code=403)
        return await call_next(request)
