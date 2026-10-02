"""The ``audit review`` page: the HF3.5 sheet plus a correct/incorrect control.

Built on :mod:`station_watch.audit.html` -- every flag value and every echoed-back
reviewer note is run through :func:`~station_watch.audit.html.esc`, so a hostile
``flags.json`` or ``verdicts.jsonl`` cannot break out of text into markup. The page
carries no inline event handlers; its one marking script carries the response's CSP
nonce (the server sends the matching ``Content-Security-Policy`` header) and posts a
verdict with ``fetch(..., {credentials: 'same-origin'})``. The session token is never
rendered into the page -- it reaches the browser only as an ``HttpOnly`` cookie.
"""

from __future__ import annotations

from station_watch.audit.flags import AuditFlag
from station_watch.audit.html import esc, flag_body

_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem;background:#111;color:#eee}"
    "section{border:1px solid #444;border-radius:6px;padding:1rem;margin:1rem 0}"
    "h2{margin-top:0;font-size:1.1rem}"
    ".meta{color:#aaa;font-size:.85rem}"
    ".verdict{margin-top:1rem;display:flex;flex-wrap:wrap;gap:.75rem;align-items:center}"
    ".verdict textarea{flex:1;min-width:16rem;background:#000;color:#eee;border:1px solid #444}"
    ".status{color:#8e8;font-size:.85rem}"
)

# One script, nonce-gated. It wires each block's Save button with addEventListener
# (no inline handler) and posts the mark as same-origin JSON; the cookie rides along.
_SCRIPT = """\
document.querySelectorAll('.verdict').forEach(function (block) {
  var save = block.querySelector('.mark');
  var status = block.querySelector('.status');
  save.addEventListener('click', function () {
    var picked = block.querySelector('input[type=radio]:checked');
    if (!picked) { status.textContent = 'pick correct or incorrect'; return; }
    fetch('/verdict', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        flag_id: block.getAttribute('data-flag-id'),
        verdict: picked.value,
        note: block.querySelector('.note').value
      })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        status.textContent = d.ok ? (d.appended ? 'saved' : 'unchanged') : 'error';
      }).catch(function () { status.textContent = 'error'; });
  });
});
"""

_NO_FLAGS = "no flags in this session"


def _radio(flag_id: str, value: str, current: str | None) -> str:
    checked = " checked" if current == value else ""
    name = f"v-{esc(flag_id)}"
    return (
        f'<label><input type="radio" name="{name}" value="{value}"{checked}> {value}</label>'
    )


def _control(flag_id: str, verdict: dict | None) -> str:
    current = verdict.get("verdict") if verdict else None
    note = verdict.get("note", "") if verdict else ""
    return (
        f'<div class="verdict" data-flag-id="{esc(flag_id)}">'
        f"{_radio(flag_id, 'correct', current)}{_radio(flag_id, 'incorrect', current)}"
        f'<textarea class="note" rows="2" placeholder="note">{esc(note)}</textarea>'
        '<button type="button" class="mark">Save</button>'
        '<span class="status"></span>'
        "</div>"
    )


def _section(flag: dict, verdict: dict | None) -> str:
    audit_flag = AuditFlag(
        flag_id=flag["flag_id"],
        kind=flag["kind"],
        target=flag["target"],
        station_id=flag["station_id"],
        run_id=flag["run_id"],
        opened_ts=flag["opened_ts"],
        closed_ts=flag.get("closed_ts"),
        frame_ids=tuple(flag.get("frame_ids") or ()),
    )
    frames = ", ".join(str(fid) for fid in audit_flag.frame_ids) or "none"
    cited = f'<div class="meta">cited frames {esc(frames)}</div>'
    return "<section>\n" + flag_body(audit_flag, []) + "\n" + cited + _control(
        flag["flag_id"], verdict
    ) + "\n</section>"


def render_review_sheet(title: str, flags: list[dict], verdicts: dict, nonce: str) -> str:
    """The whole review page: head, one section with a control per flag, one nonce'd script."""
    if flags:
        body = "\n".join(_section(flag, verdicts.get(flag["flag_id"])) for flag in flags)
    else:
        body = f'<p class="meta">{esc(_NO_FLAGS)}</p>'
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        f"<title>{esc(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        f"</head>\n<body>\n<h1>{esc(title)}</h1>\n{body}\n"
        f'<script nonce="{esc(nonce)}">\n{_SCRIPT}</script>\n'
        "</body>\n</html>\n"
    )


__all__ = ["render_review_sheet"]
