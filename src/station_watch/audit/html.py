"""The one helper that produces audit HTML -- and escapes everything it is given.

Every value a sheet interpolates (flag ids, kinds, targets, blind reasons, station
and run ids, timestamps) comes from the Log and is treated as hostile input: it is
run through :func:`esc` (``html.escape(..., quote=True)``) before it reaches the
markup. The *only* path that is not escaped is the ``src`` of a thumbnail, and that
is a ``data:`` URI this module builds itself from JPEG bytes (:func:`data_uri`) --
never a value from the Log. A static sheet carries no script at all and a strict
Content-Security-Policy meta tag, so even an escape slip has nothing to execute.

HF3.6 and HF3.7 render their sheets through this same module; it is the only way
audit HTML is produced.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass

from station_watch.audit.flags import AuditFlag
from station_watch.clock import parse_iso

# Strict policy for a static, offline proof sheet: no network, images only as data
# URIs, inline styles only (the sheet ships one <style> block), no base or forms.
_CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)
_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem;background:#111;color:#eee}"
    "section{border:1px solid #444;border-radius:6px;padding:1rem;margin:1rem 0}"
    "h2{margin-top:0;font-size:1.1rem}"
    ".meta{color:#aaa;font-size:.85rem}"
    ".frames{display:flex;flex-wrap:wrap;gap:1rem;margin-top:1rem}"
    ".frame{border:1px solid #333;padding:.5rem;border-radius:4px}"
    ".frame img{display:block;max-width:360px;height:auto}"
    ".missing,.nooverlay{color:#e88;font-size:.85rem}"
)

_NO_FLAGS = "no flags in this session"


@dataclass(frozen=True)
class FrameCell:
    """One cited frame as the sheet will show it (built by the thumbnail resolver)."""

    frame_id: int
    capture_ts: str | None
    jpeg: bytes | None  # the overlaid thumbnail, or None when there is no evidence
    overlay_available: bool  # a marker was found, so zones are drawn
    missing_reason: str | None  # the MissingEvidence reason, when jpeg is None


def esc(value) -> str:
    """HTML-escape any value (including quotes), coercing to ``str`` first."""
    return html.escape(str(value), quote=True)


def data_uri(jpeg: bytes) -> str:
    """A ``data:image/jpeg;base64`` URI built from JPEG bytes this module controls."""
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def render_sheet(title: str, flags: list[tuple[AuditFlag, list[FrameCell]]]) -> str:
    """The whole proof sheet: head (with CSP), one section per flag, escaped throughout."""
    if flags:
        body = "\n".join(_flag_section(flag, cells) for flag, cells in flags)
    else:
        body = f'<p class="meta">{esc(_NO_FLAGS)}</p>'
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">\n'
        f"<title>{esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        f"</head>\n<body>\n<h1>{esc(title)}</h1>\n{body}\n</body>\n</html>\n"
    )


def _verdict_state(kind: str) -> str:
    return "unobservable" if kind.startswith("unobservable:") else "fault"


def _duration(opened_ts: str, closed_ts: str | None) -> str:
    if closed_ts is None:
        return "ongoing (never closed)"
    seconds = (parse_iso(closed_ts) - parse_iso(opened_ts)).total_seconds()
    return f"{seconds:.3f}s"


def flag_body(flag: AuditFlag, cells: list[FrameCell]) -> str:
    """The escaped inner HTML for one flag -- its heading, metadata and frame cells.

    The shared body both the static ``audit build`` sheet and the HF3.6 ``audit
    review`` page wrap in their own ``<section>`` (review adds a verdict control). Every
    interpolated value is run through :func:`esc`.
    """
    closed = flag.closed_ts if flag.closed_ts is not None else "—"
    rows = [
        f"<h2>{esc(flag.kind)} &middot; {esc(flag.target)}</h2>",
        '<div class="meta">'
        f"flag {esc(flag.flag_id)}<br>"
        f"station {esc(flag.station_id)} &middot; run {esc(flag.run_id)}<br>"
        f"verdict state {esc(_verdict_state(flag.kind))}<br>"
        f"opened {esc(flag.opened_ts)} &middot; closed {esc(closed)} &middot; "
        f"duration {esc(_duration(flag.opened_ts, flag.closed_ts))}"
        "</div>",
    ]
    if cells:
        rows.append('<div class="frames">' + "".join(_frame_cell(c) for c in cells) + "</div>")
    return "\n".join(rows)


def _flag_section(flag: AuditFlag, cells: list[FrameCell]) -> str:
    return "<section>\n" + flag_body(flag, cells) + "\n</section>"


def _frame_cell(cell: FrameCell) -> str:
    ts = esc(cell.capture_ts) if cell.capture_ts is not None else "—"
    head = f'<div class="meta">frame {esc(cell.frame_id)} &middot; ts {ts}</div>'
    if cell.jpeg is None:
        reason = esc(cell.missing_reason or "missing")
        return f'<div class="frame">{head}<div class="missing">no evidence: {reason}</div></div>'
    img = f'<img alt="cited frame {esc(cell.frame_id)}" src="{data_uri(cell.jpeg)}">'
    if cell.overlay_available:
        return f'<div class="frame">{head}{img}</div>'
    note = '<div class="nooverlay">overlay unavailable: fiducial not found</div>'
    return f'<div class="frame">{head}{img}{note}</div>'


__all__ = ["esc", "data_uri", "render_sheet", "flag_body", "FrameCell"]
