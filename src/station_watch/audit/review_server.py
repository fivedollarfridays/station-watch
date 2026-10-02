"""The ``audit review`` HTTP face: a stdlib threading server bound to 127.0.0.1.

``GET /`` serves the HF3.5 sheet with a correct/incorrect control per flag, but only
with the session token: ``GET /?token=<token>`` (the URL printed once at startup)
sets it as an ``HttpOnly`` cookie and redirects to ``/``; after that the cookie (or
an ``X-Audit-Token`` header) serves the page. No token is 401, a wrong one 403.
``POST /verdict`` records a mark. Every
response carries a fresh-nonce ``Content-Security-Policy`` (the one marking script
carries the matching nonce), ``X-Content-Type-Options: nosniff`` and
``Cache-Control: no-store``. A ``POST`` is accepted only with the token (cookie or
``X-Audit-Token`` header, constant-time compared), an ``application/json`` body, a
loopback ``Host`` and an absent-or-own ``Origin`` -- otherwise 403/415. Unknown flag
ids and bad verdicts are 400; every method other than GET and POST is 405. The server
binds only to loopback and never writes the Log; marks land in ``verdicts.jsonl``.
"""

from __future__ import annotations

import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import station_watch.audit.review_guards as guards
from station_watch.audit.review_html import render_review_sheet
from station_watch.audit.verdicts import VALID_VERDICTS, append_mark, effective
from station_watch.clock import utc_now_iso
from station_watch.logsafe import loggable_path

REQUEST_TIMEOUT_S = 10.0
NONCE_BYTES = 16


# A verdict body is a flag id, a verdict and a short note: a few hundred bytes.
MAX_BODY_BYTES = 64 * 1024


class ReviewHandler(BaseHTTPRequestHandler):
    """Serves the review page and records verdicts; one marking endpoint, loopback only."""

    timeout = REQUEST_TIMEOUT_S

    def do_GET(self) -> None:  # noqa: N802 (stdlib dispatch name)
        if not self._loopback_ok():
            return
        route, _, query = self.path.partition("?")
        if route not in ("/", "/index.html"):
            self._send(404, "text/plain; charset=utf-8", "not found\n")
            return
        access = guards.page_access(self.headers, query, self.server.token)
        if access == "login":  # one-time sign-in: set the cookie, drop the token from the URL
            cookie = guards.token_cookie(self.server.token)
            self._send(
                303, "text/plain; charset=utf-8", "", {"Location": "/", "Set-Cookie": cookie}
            )
        elif access == "ok":
            self._serve_page()
        elif access == "missing":
            self._log_rejected(401, "no token")
            self._send(401, "text/plain; charset=utf-8", "open the URL printed at startup\n")
        else:
            self._deny("forbidden token")

    def do_POST(self) -> None:  # noqa: N802 (stdlib dispatch name)
        if not self._loopback_ok():
            return
        if self.path.split("?", 1)[0] != "/verdict":
            self._send(404, "text/plain; charset=utf-8", "not found\n")
            return
        self._handle_verdict()

    def _loopback_ok(self) -> bool:
        port = self.server.server_address[1]
        if not guards.host_allowed(self.headers.get("Host"), port):
            self._deny("forbidden host")
            return False
        if not guards.origin_allowed(self.headers.get("Origin"), port):
            self._deny("forbidden origin")
            return False
        return True

    def _serve_page(self) -> None:
        nonce = secrets.token_urlsafe(NONCE_BYTES)
        page = render_review_sheet(
            self.server.title, self.server.flags, effective(self.server.audit_dir), nonce
        )
        csp = {"Content-Security-Policy": guards.csp_header(nonce)}
        self._send(200, "text/html; charset=utf-8", page, csp)

    def _handle_verdict(self) -> None:
        if not guards.token_ok(guards.token_from_request(self.headers), self.server.token):
            self._deny("forbidden token")
            return
        if not guards.json_content_type(self.headers.get("Content-Type")):
            self._json(415, {"ok": False, "error": "content-type must be application/json"})
            return
        length = guards.content_length(self.headers.get("Content-Length"))
        if length is None:
            self._json(400, {"ok": False, "error": "bad Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._json(413, {"ok": False, "error": f"body over {MAX_BODY_BYTES} bytes"})
            return
        raw = self.rfile.read(length) if length > 0 else b""
        data = guards.parse_json_body(raw)
        if data is None:
            self._json(400, {"ok": False, "error": "body is not JSON"})
            return
        self._record(data)

    def _record(self, data: dict) -> None:
        flag_id = data.get("flag_id")
        verdict = data.get("verdict")
        note = data.get("note") or ""
        if not isinstance(flag_id, str) or flag_id not in self.server.flag_ids:
            self._json(400, {"ok": False, "error": f"unknown flag_id {flag_id!r}"})
            return
        if verdict not in VALID_VERDICTS:
            self._json(400, {"ok": False, "error": f"verdict must be one of {VALID_VERDICTS}"})
            return
        appended = append_mark(
            self.server.audit_dir,
            flag_id,
            verdict,
            note,
            self.server.reviewer,
            ts=self.server.clock(),
        )
        self._json(200, {"ok": True, "flag_id": flag_id, "appended": appended})

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, content_type: str, body: str, extra=None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, body: dict) -> None:
        if status != 200:
            self._log_rejected(status, body.get("error", ""))
        self._send(status, "application/json", json.dumps(body))

    def _deny(self, reason: str) -> None:
        self._log_rejected(403, reason)
        self._send(403, "text/plain; charset=utf-8", f"{reason}\n")

    def _reject(self) -> None:
        self._log_rejected(405, "method not allowed")
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _log_rejected(self, status: int, reason: str) -> None:
        sys.stderr.write(
            f"station-watch audit review: {status} {loggable_path(self.command)} "
            f"{loggable_path(self.path)} ({reason})\n"
        )

    def __getattr__(self, name: str):
        # Route every method other than GET/POST to 405 (the stdlib would answer 501).
        if name.startswith("do_"):
            return self._reject
        raise AttributeError(name)

    def log_message(self, *args) -> None:  # keep successful requests quiet on stderr
        pass


def make_review_server(
    audit_dir, flags, flag_ids, reviewer, *, port=8766, token=None, clock=utc_now_iso
):
    """A threading HTTP server bound to loopback that serves ``audit_dir``'s review sheet."""
    server = ThreadingHTTPServer((guards.LOOPBACK, port), ReviewHandler)
    server.daemon_threads = True
    server.audit_dir = audit_dir
    server.flags = flags
    server.flag_ids = set(flag_ids)
    server.reviewer = reviewer
    server.token = token if token is not None else secrets.token_urlsafe(32)
    server.clock = clock
    server.title = f"station-watch audit review — {audit_dir}"
    return server


__all__ = ["ReviewHandler", "make_review_server", "REQUEST_TIMEOUT_S", "MAX_BODY_BYTES"]
