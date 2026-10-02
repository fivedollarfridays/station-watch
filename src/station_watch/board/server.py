"""The Board's HTTP face: a stdlib threading ``http.server`` bound to 127.0.0.1.

``GET /`` serves the auto-refreshing HTML page; ``GET /view.json`` serves the
same view as JSON. Every other path is 404 and every non-GET method (including
ones the stdlib would answer 501, such as PATCH or HEAD) is 405 -- the Board is
read-only, so there is no method that mutates anything.

The server binds only to the loopback address, never a routable interface. A GET
whose ``Host`` header is not ``127.0.0.1:<port>`` or ``localhost:<port>``, or
whose ``Origin`` is not the Board's own loopback origin, is 403 -- so a DNS-rebound
or cross-origin page in the operator's browser cannot read the station's state in
code, not merely for the absence of CORS headers. Every response carries
``X-Content-Type-Options: nosniff`` and ``Cache-Control: no-store``.

It is a :class:`~http.server.ThreadingHTTPServer` with daemon threads and a
per-request socket timeout, so one stalled connection cannot starve the
operator's page. Rejections (403, 405) write one line on stderr; successful GETs
stay quiet.

Each request rebuilds the view from the Log through a fresh read-only reader, so
the page always reflects the Log as it is now and the server holds no writable
handle onto it.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from station_watch.board.render import render_html, render_view_json
from station_watch.board.view import build_view
from station_watch.clock import utc_now_iso

LOOPBACK = "127.0.0.1"
REQUEST_TIMEOUT_S = 10.0


class BoardHandler(BaseHTTPRequestHandler):
    """Serves the Board page and JSON view; rejects every non-GET method with 405."""

    timeout = REQUEST_TIMEOUT_S  # per-request socket timeout (StreamRequestHandler)

    def _view(self):
        return build_view(
            self.server.board_config, self.server.board_log_path, now=self.server.board_clock()
        )

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, content_type: str, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _host_allowed(self) -> bool:
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in (f"{LOOPBACK}:{port}", f"localhost:{port}")

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # a same-document / non-browser request sends no Origin
        port = self.server.server_address[1]
        allowed = (f"http://{LOOPBACK}:{port}", f"http://localhost:{port}")
        return origin.strip().lower() in allowed

    def do_GET(self) -> None:  # noqa: N802 (stdlib dispatch name)
        if not self._host_allowed():
            self._deny("forbidden host")
            return
        if not self._origin_allowed():
            self._deny("forbidden origin")
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", render_html(self._view()))
        elif path in ("/view.json", "/view"):
            self._send(200, "application/json", json.dumps(render_view_json(self._view())))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found\n")

    def _deny(self, reason: str) -> None:
        self._log_rejected(403, reason)
        self._send(403, "text/plain; charset=utf-8", f"{reason}\n")

    def _reject(self) -> None:
        self._log_rejected(405, "method not allowed")
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self._security_headers()
        self.end_headers()

    def _log_rejected(self, status: int, reason: str) -> None:
        # One line, no request headers: method, path, status, reason.
        sys.stderr.write(f"station-watch board: {status} {self.command} {self.path} ({reason})\n")

    def __getattr__(self, name: str):
        # The stdlib dispatches method X to ``do_X`` and answers 501 when it is
        # missing; route every method other than GET to 405 instead.
        if name.startswith("do_"):
            return self._reject
        raise AttributeError(name)

    def log_message(self, *args) -> None:  # keep the Board quiet on stderr
        pass


def make_server(config, log_path, *, port: int = 8765, clock=utc_now_iso) -> HTTPServer:
    """A threading HTTPServer bound to loopback that serves ``config``'s station."""
    server = ThreadingHTTPServer((LOOPBACK, port), BoardHandler)
    server.daemon_threads = True
    server.board_config = config
    server.board_log_path = log_path
    server.board_clock = clock
    return server


__all__ = ["BoardHandler", "make_server", "LOOPBACK"]
