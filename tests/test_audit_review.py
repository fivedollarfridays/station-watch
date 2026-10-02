"""``audit review``: a token-gated, loopback-only verdict server with a CSP'd page.

Covers the proving test (a real subprocess accepting a POST by cookie and by header,
and refusing a POST without the token, with a wrong token/Host/Origin, or with a
non-JSON Content-Type), the XSS defences on the served page, the Set-Cookie flags and
token shape, the 400s for an unknown flag / invalid verdict, re-marking over HTTP, and
that the server binds only to loopback.
"""

from __future__ import annotations

import http.client
import json
import re
import subprocess
import sys
import threading
from html.parser import HTMLParser
from pathlib import Path

from station_watch.audit.review_server import make_review_server

RUN = "run-review"
FID = f"{RUN}:missing_part:rail_pos_1:1"
TS = "2026-10-01T00:00:00.000000+00:00"


def _flag(flag_id=FID, kind="missing_part", target="rail_pos_1") -> dict:
    return {
        "flag_id": flag_id,
        "kind": kind,
        "target": target,
        "station_id": "station-1",
        "run_id": RUN,
        "opened_ts": TS,
        "closed_ts": None,
        "frame_ids": [1, 2],
    }


def _audit_dir(tmp_path, flags=None) -> Path:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "flags.json").write_text(json.dumps(flags if flags is not None else [_flag()]) + "\n")
    return audit


def _lines(audit: Path) -> list[str]:
    path = audit / "verdicts.jsonl"
    return [ln for ln in path.read_text().splitlines() if ln.strip()] if path.exists() else []


class _Served:
    def __init__(self, audit, token="tok-fixed-value", reviewer="alice"):
        flags = json.loads((audit / "flags.json").read_text())
        self.server = make_review_server(
            audit,
            flags,
            [f["flag_id"] for f in flags],
            reviewer,
            port=0,
            token=token,
            clock=lambda: TS,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server.server_address[1]

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _request(
    port,
    *,
    method="GET",
    path="/",
    host=None,
    origin=None,
    cookie=None,
    token=None,
    content_type=None,
    body=None,
):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
        if origin is not None:
            conn.putheader("Origin", origin)
        if cookie is not None:
            conn.putheader("Cookie", cookie)
        if token is not None:
            conn.putheader("X-Audit-Token", token)
        if content_type is not None:
            conn.putheader("Content-Type", content_type)
        raw = body.encode() if body is not None else b""
        conn.putheader("Content-Length", str(len(raw)))
        conn.endheaders()
        if raw:
            conn.send(raw)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _post(port, **kw):
    kw.setdefault("method", "POST")
    kw.setdefault("path", "/verdict")
    kw.setdefault("content_type", "application/json")
    kw.setdefault("body", json.dumps({"flag_id": FID, "verdict": "correct", "note": ""}))
    return _request(port, **kw)


# --------------------------------------------------------------------------- #
# Set-Cookie flags + token shape (token_urlsafe(32) is 43 chars, differs per start)
# --------------------------------------------------------------------------- #
def test_set_cookie_is_httponly_samesite_strict_and_token_is_43_urlsafe_chars(tmp_path):
    audit = _audit_dir(tmp_path)
    s = make_review_server(audit, [_flag()], [FID], "alice", port=0)
    try:
        assert len(s.token) == 43 and re.fullmatch(r"[A-Za-z0-9_-]+", s.token)
    finally:
        s.server_close()
    other = make_review_server(audit, [_flag()], [FID], "alice", port=0)
    try:
        assert other.token != s.token  # a fresh token each server start
    finally:
        other.server_close()


def test_get_sets_an_httponly_strict_cookie_without_leaking_the_token_into_the_body(tmp_path):
    audit = _audit_dir(tmp_path)
    with _Served(audit, token="tok-fixed-value") as port:
        # The sign-in URL sets the cookie and redirects to a token-free URL.
        status, headers, _ = _request(port, path="/?token=tok-fixed-value")
        assert status == 303 and headers["Location"] == "/"
        cookie = headers["Set-Cookie"]
        assert "audit_token=tok-fixed-value" in cookie
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
        status, _, body = _request(port, path="/", cookie=cookie.split(";", 1)[0])
        assert status == 200
        assert b"tok-fixed-value" not in body  # token is never rendered into the page


# --------------------------------------------------------------------------- #
# XSS: one nonce'd script, hostile strings escaped, CSP header, token absent
# --------------------------------------------------------------------------- #
class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = 0
        self.script_nonce = None
        self.on_attrs = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        for name, _value in attrs:
            if name.lower().startswith("on"):
                self.on_attrs.append((tag, name))
        if tag == "script":
            self.scripts += 1
            self.script_nonce = dict(attrs).get("nonce")


def test_xss_hostile_flag_and_note_are_escaped_with_one_nonced_script(tmp_path):
    hostile = "<script>alert(1)</script>"
    note = '"><img src=x onerror=alert(1)>'
    flag = _flag(flag_id=f"{RUN}:blind:{hostile}:1", kind=f"unobservable:{hostile}", target=hostile)
    audit = _audit_dir(tmp_path, [flag])
    (audit / "verdicts.jsonl").write_text(
        json.dumps(
            {
                "flag_id": flag["flag_id"],
                "verdict": "incorrect",
                "note": note,
                "reviewer": "alice",
                "ts": TS,
            }
        )
        + "\n"
    )
    with _Served(audit, token="tok-xss-secret") as port:
        status, headers, raw = _request(port, path="/", token="tok-xss-secret")
    assert status == 200
    body = raw.decode()

    page = _Page()
    page.feed(body)
    assert page.scripts == 1, "exactly one script element (the marking script)"
    assert page.on_attrs == [], page.on_attrs  # no attribute starting with on*

    csp = headers["Content-Security-Policy"]
    assert csp.startswith("default-src 'self'; script-src 'nonce-")
    assert "object-src 'none'" in csp and "frame-ancestors 'none'" in csp
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
    assert page.script_nonce == nonce, "the script carries the response's CSP nonce"

    assert hostile not in body and note not in body  # hostile strings never land raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body  # only as escaped text
    assert b"tok-xss-secret" not in raw  # token appears nowhere in the body


def test_every_html_response_carries_nosniff_and_no_store(tmp_path):
    audit = _audit_dir(tmp_path)
    with _Served(audit) as port:
        status, headers, _ = _request(port, path="/", token="tok-fixed-value")
        assert status == 200
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cache-Control"] == "no-store"


# --------------------------------------------------------------------------- #
# 400s: unknown flag id, invalid verdict
# --------------------------------------------------------------------------- #
def test_unknown_flag_and_invalid_verdict_get_400_and_append_nothing(tmp_path):
    audit = _audit_dir(tmp_path)
    with _Served(audit, token="tok") as port:
        unknown = _post(
            port, token="tok", body=json.dumps({"flag_id": "nope", "verdict": "correct"})
        )
        assert unknown[0] == 400
        bad = _post(port, token="tok", body=json.dumps({"flag_id": FID, "verdict": "maybe"}))
        assert bad[0] == 400
        not_json = _post(port, token="tok", body="not json at all")
        assert not_json[0] == 400
    assert _lines(audit) == [], "no 400 request writes a verdict line"


def test_re_marking_over_http_changes_the_effective_verdict_and_appends_one_line(tmp_path):
    audit = _audit_dir(tmp_path)
    with _Served(audit, token="tok") as port:
        first = _post(
            port, token="tok", body=json.dumps({"flag_id": FID, "verdict": "correct", "note": ""})
        )
        assert first[0] == 200 and json.loads(first[2])["appended"] is True
        same = _post(
            port, token="tok", body=json.dumps({"flag_id": FID, "verdict": "correct", "note": ""})
        )
        assert same[0] == 200 and json.loads(same[2])["appended"] is False
        changed = _post(
            port,
            token="tok",
            body=json.dumps({"flag_id": FID, "verdict": "incorrect", "note": "x"}),
        )
        assert changed[0] == 200 and json.loads(changed[2])["appended"] is True
    lines = [json.loads(ln) for ln in _lines(audit)]
    assert len(lines) == 2
    assert lines[-1]["verdict"] == "incorrect" and lines[-1]["reviewer"] == "alice"


def test_methods_other_than_get_and_post_are_405(tmp_path):
    audit = _audit_dir(tmp_path)
    with _Served(audit) as port:
        status, headers, _ = _request(port, method="PUT", path="/verdict")
        assert status == 405
        assert "GET" in headers["Allow"] and "POST" in headers["Allow"]


def test_server_binds_only_to_loopback(tmp_path):
    audit = _audit_dir(tmp_path)
    s = make_review_server(audit, [_flag()], [FID], "alice", port=0)
    try:
        assert s.server_address[0] == "127.0.0.1"
    finally:
        s.server_close()


# --------------------------------------------------------------------------- #
# Proving test (AC1): a real subprocess server, cookie and header auth, bad cases
# --------------------------------------------------------------------------- #
def _start_subprocess_server(audit):
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "station_watch",
            "audit",
            "review",
            "--audit",
            str(audit),
            "--port",
            "0",
            "--reviewer",
            "carol",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    serving = proc.stdout.readline()
    port = int(re.search(r"http://127\.0\.0\.1:(\d+)/", serving).group(1))
    # The sign-in URL is printed once, on stderr: the operator's only way in.
    token = re.search(r"/\?token=(\S+)", proc.stderr.readline()).group(1)
    return proc, port, token


def test_proving_subprocess_accepts_token_by_cookie_and_header_refuses_otherwise(tmp_path):
    audit = _audit_dir(
        tmp_path, [_flag(), _flag(flag_id=f"{RUN}:missing_part:rail_pos_2:2", target="rail_pos_2")]
    )
    fid2 = f"{RUN}:missing_part:rail_pos_2:2"
    proc, port, token = _start_subprocess_server(audit)
    try:
        assert _request(port, path="/")[0] == 401  # no token: the page is not served
        # The cookie the browser gets from the printed sign-in URL.
        status, headers, _ = _request(port, path=f"/?token={token}")
        assert status == 303
        cookie = headers["Set-Cookie"].split(";", 1)[0]  # audit_token=<token>
        status, _, body = _request(port, path="/", cookie=cookie)
        assert status == 200 and token not in body.decode()

        by_cookie = _post(
            port, cookie=cookie, body=json.dumps({"flag_id": FID, "verdict": "correct"})
        )
        assert by_cookie[0] == 200, by_cookie
        by_header = _post(
            port, token=token, body=json.dumps({"flag_id": fid2, "verdict": "incorrect"})
        )
        assert by_header[0] == 200, by_header
        assert len(_lines(audit)) == 2
        marks = [json.loads(ln) for ln in _lines(audit)]
        assert all(m["reviewer"] == "carol" and m["ts"] for m in marks)

        before = len(_lines(audit))
        assert _post(port)[0] == 403  # no token
        assert _post(port, token="wrong")[0] == 403
        assert _post(port, token=token, host="evil.example")[0] == 403
        assert _post(port, token=token, origin="http://evil.example")[0] == 403
        assert _post(port, token=token, content_type="text/plain")[0] == 415
        assert len(_lines(audit)) == before, "no rejected POST appends a line"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
