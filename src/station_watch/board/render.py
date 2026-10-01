"""Render a :class:`~station_watch.board.view.StationView` three ways.

* :func:`render_text` -- the plain-text screen ``board --once`` prints.
* :func:`render_view_json` -- a JSON-serialisable dict, the HTTP ``/view.json``.
* :func:`render_html` -- one self-contained, auto-refreshing HTML page.

All three render every fault flag and every alarm episode by its raw name, so a
kind the Board does not special-case still shows (never silently dropped) and an
UNKNOWN or STALE station always reads as NOT OK, never as healthy (K1).
"""

from __future__ import annotations

from html import escape

from station_watch.board.view import StationView

_REFRESH_S = 2


def _status(view: StationView) -> str:
    return "OK" if view.ok else "NOT OK"


def render_text(view: StationView) -> str:
    """The plain-text view for ``board --once``."""
    header = f"Station {view.station_id} — {view.state}"
    if view.state_stale and view.state != "UNKNOWN":
        header += " STALE"
    lines = [
        header,
        f"  status: {_status(view)}",
        f"  state: {view.state_detail}",
        f"  frames: {view.frame_detail}",
        f"  blind: {', '.join(view.blind_reasons) or 'none'}",
    ]
    if view.flags:
        lines.append("  flags:")
        for flag in view.flags:
            ids = ",".join(str(i) for i in flag.frame_ids)
            lines.append(f"    - {flag.kind} {flag.target} frames=[{ids}]")
    else:
        lines.append("  flags: none")
    episodes = ", ".join(view.alarm_open_episodes) or "none"
    lines.append(f"  alarm: open episodes: {episodes} ({view.alarm_detail})")
    return "\n".join(lines)


def render_view_json(view: StationView) -> dict:
    """A JSON-serialisable dict of the view, for the HTTP JSON endpoint."""
    return {
        "station_id": view.station_id,
        "ok": view.ok,
        "state": view.state,
        "state_detail": view.state_detail,
        "state_held_s": view.state_held_s,
        "state_stale": view.state_stale,
        "frame_age_s": view.frame_age_s,
        "frame_stale": view.frame_stale,
        "frame_detail": view.frame_detail,
        "blind_reasons": list(view.blind_reasons),
        "flags": [
            {"kind": f.kind, "target": f.target, "frame_ids": list(f.frame_ids)} for f in view.flags
        ],
        "alarm_open_episodes": list(view.alarm_open_episodes),
        "alarm_age_s": view.alarm_age_s,
        "alarm_stale": view.alarm_stale,
        "alarm_detail": view.alarm_detail,
    }


def _flag_html(view: StationView) -> str:
    if not view.flags:
        return "<li>none</li>"
    items = []
    for flag in view.flags:
        ids = ",".join(str(i) for i in flag.frame_ids)
        items.append(f"<li>{escape(flag.kind)} {escape(flag.target)} frames=[{escape(ids)}]</li>")
    return "".join(items)


def render_html(view: StationView) -> str:
    """One self-contained HTML page that refreshes itself every few seconds."""
    blind = escape(", ".join(view.blind_reasons) or "none")
    episodes = escape(", ".join(view.alarm_open_episodes) or "none")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta http-equiv='refresh' content='{_REFRESH_S}'>"
        f"<title>Station Watch — {escape(view.station_id)}</title></head><body>"
        f"<h1>Station {escape(view.station_id)} — {escape(view.state)}</h1>"
        f"<p class='status'>status: <b>{escape(_status(view))}</b></p>"
        f"<p class='state'>state: {escape(view.state_detail)}</p>"
        f"<p class='frames'>frames: {escape(view.frame_detail)}</p>"
        f"<p class='blind'>blind: {blind}</p>"
        f"<p>flags:</p><ul class='flags'>{_flag_html(view)}</ul>"
        f"<p class='alarm'>alarm: open episodes: {episodes} ({escape(view.alarm_detail)})</p>"
        "</body></html>"
    )


__all__ = ["render_text", "render_view_json", "render_html"]
