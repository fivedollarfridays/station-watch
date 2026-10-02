"""The checks that gate the review server: Host, Origin, token, Content-Type, CSP.

Kept as free functions beside the handler so each is unit-testable without a socket
and so the server module stays within its function-count budget. A loopback-only
``Host``, a same-origin-or-absent ``Origin``, a constant-time token compare and an
``application/json`` body are all that let a mark through -- so a DNS-rebound or
cross-origin page in the operator's browser can neither read nor write the sheet.
"""

from __future__ import annotations

import hmac
import http.cookies
import json
import urllib.parse

LOOPBACK = "127.0.0.1"
TOKEN_COOKIE = "audit_token"
TOKEN_HEADER = "X-Audit-Token"

# Nonce-gated scripts, loopback connect/img, no base/object; forms stay same-origin.
_CSP = (
    "default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def host_allowed(host_header: str | None, port: int) -> bool:
    """True only for a ``Host`` of ``127.0.0.1:<port>`` or ``localhost:<port>``."""
    host = (host_header or "").strip().lower()
    return host in (f"{LOOPBACK}:{port}", f"localhost:{port}")


def origin_allowed(origin_header: str | None, port: int) -> bool:
    """True when ``Origin`` is absent (same-document/non-browser) or the server's own."""
    if origin_header is None:
        return True
    allowed = (f"http://{LOOPBACK}:{port}", f"http://localhost:{port}")
    return origin_header.strip().lower() in allowed


def token_from_request(headers) -> str | None:
    """The presented token: the ``X-Audit-Token`` header, else the ``audit_token`` cookie."""
    header = headers.get(TOKEN_HEADER)
    if header is not None:
        return header
    raw = headers.get("Cookie")
    if not raw:
        return None
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(raw)
    except http.cookies.CookieError:
        return None
    morsel = jar.get(TOKEN_COOKIE)
    return morsel.value if morsel else None


def token_ok(presented: str | None, expected: str) -> bool:
    """Constant-time compare of a presented token with the server's, False when absent."""
    if presented is None:
        return False
    return hmac.compare_digest(presented, expected)


def page_access(headers, query: str, expected: str) -> str:
    """Who may see the page: ``login`` (a valid ``?token=``), ``ok``, ``missing`` or ``wrong``.

    A ``?token=`` in the URL is the one-time sign-in from the URL printed at startup;
    otherwise the cookie or ``X-Audit-Token`` header must match (constant time).
    """
    from_query = urllib.parse.parse_qs(query).get("token")
    if from_query:
        return "login" if token_ok(from_query[0], expected) else "wrong"
    presented = token_from_request(headers)
    if presented is None:
        return "missing"
    return "ok" if token_ok(presented, expected) else "wrong"


def token_cookie(token: str) -> str:
    """The ``Set-Cookie`` value that carries the session token (HttpOnly, SameSite=Strict)."""
    return f"{TOKEN_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"


def json_content_type(content_type: str | None) -> bool:
    """True only when the media type is ``application/json`` (a charset param is allowed)."""
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media == "application/json"


def csp_header(nonce: str) -> str:
    """The Content-Security-Policy value for one HTML response, binding its script nonce."""
    return _CSP.format(nonce=nonce)


def content_length(header: str | None) -> int | None:
    """The request's Content-Length as a non-negative int, or None when malformed."""
    if header is None or header.strip() == "":
        return 0
    try:
        length = int(header)
    except ValueError:
        return None
    return length if length >= 0 else None


def parse_json_body(raw: bytes) -> dict | None:
    """Decode a request body into a JSON object, or ``None`` if it is not one."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):  # deep nesting is bad input
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "page_access",
    "token_cookie",
    "content_length",
    "LOOPBACK",
    "TOKEN_COOKIE",
    "TOKEN_HEADER",
    "host_allowed",
    "origin_allowed",
    "token_from_request",
    "token_ok",
    "json_content_type",
    "csp_header",
    "parse_json_body",
]
