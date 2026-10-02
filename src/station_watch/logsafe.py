"""Make a request path safe to write into a one-line stderr log.

Both loopback HTTP faces (the Board and ``audit review``) log rejected requests as
one line. The path is attacker-chosen: its query string may carry a token (so it is
dropped) and it may hold control characters that forge or repaint log lines (so
every non-printable character is escaped as ``\\xNN``). The result is also capped.
"""

from __future__ import annotations

_MAX_LOGGED = 200


def loggable_path(path: str) -> str:
    """``path`` without its query string, non-printables escaped, at most 200 chars."""
    route = path.split("?", 1)[0]
    escaped = "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in route)
    return escaped[:_MAX_LOGGED]


__all__ = ["loggable_path"]
