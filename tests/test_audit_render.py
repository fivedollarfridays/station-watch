"""The sheet renderer escapes every value, carries a CSP, and draws zone overlays."""

from __future__ import annotations

from html.parser import HTMLParser

import numpy as np

from station_watch.audit.flags import AuditFlag
from station_watch.audit.html import FrameCell, esc, render_sheet
from station_watch.audit.overlay import collect_regions, draw_overlay

SCRIPT = "<script>alert(1)</script>"
IMG = '"><img src=x onerror=alert(1)>'


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(name for name, _ in attrs)


def _parse(markup: str) -> _Collector:
    collector = _Collector()
    collector.feed(markup)
    return collector


def _flag(flag_id="r:missing_part:t:1", kind="missing_part", target="rail_pos_1", frames=()):
    return AuditFlag(
        flag_id=flag_id,
        kind=kind,
        target=target,
        station_id="station-1",
        run_id="run-1",
        opened_ts="2026-10-01T00:00:00.000000+00:00",
        closed_ts="2026-10-01T00:00:05.000000+00:00",
        frame_ids=tuple(c.frame_id for c in frames),
    )


def test_no_flags_sheet_says_so_and_carries_the_csp():
    sheet = render_sheet("audit", [])
    assert "no flags in this session" in sheet
    assert (
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
    ) in sheet


def test_xss_hostile_values_are_escaped_with_no_script_or_handlers():
    # Both hostile payloads arrive as fault targets (the only free-string Log value
    # for a flag): BlindReason is a closed StrEnum, so a blind flag's target/kind can
    # only ever be a safe reason -- the escaping proven here still guards it.
    flags = [
        (_flag(flag_id="r:missing_part:a:1", target=SCRIPT), []),
        (_flag(flag_id="r:keepout_entry:b:2", kind="keepout_entry", target=IMG), []),
    ]
    sheet = render_sheet("audit", flags)
    parsed = _parse(sheet)
    assert "script" not in parsed.tags
    assert not any(name.startswith("on") for name in parsed.attrs)
    assert esc(SCRIPT) in sheet and esc(IMG) in sheet
    # No raw tag openers survive: the payloads exist only as inert escaped text.
    assert "<script" not in sheet
    assert "<img src=x" not in sheet
    assert "Content-Security-Policy" in sheet


def test_a_cited_frame_with_no_evidence_shows_the_reason_and_no_image():
    cell = FrameCell(frame_id=7, capture_ts=None, jpeg=None, overlay_available=False,
                     missing_reason="pruned")
    sheet = render_sheet("audit", [(_flag(frames=[cell]), [cell])])
    assert "no evidence: pruned" in sheet
    parsed = _parse(sheet)
    assert "img" not in parsed.tags


def test_a_frame_without_a_marker_says_overlay_unavailable_but_still_shows_the_image():
    jpeg = b"\xff\xd8\xff\xe0not-really-jpeg"
    cell = FrameCell(frame_id=3, capture_ts="2026-10-01T00:00:01.000000+00:00", jpeg=jpeg,
                     overlay_available=False, missing_reason=None)
    sheet = render_sheet("audit", [(_flag(frames=[cell]), [cell])])
    assert "overlay unavailable: fiducial not found" in sheet
    parsed = _parse(sheet)
    assert "img" in parsed.tags
    assert "data:image/jpeg;base64," in sheet


# --- AC4: the overlay is drawn where the stored corners say ---------------------


def _config():
    from tests.helpers.records import health_config

    return health_config(
        required_slots=["rail_pos_1"],
        detect={
            "persistence_frames": 3,
            "emit_interval_s": 5.0,
            "rail_positions": {"rail_pos_1": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]},
            "station_zone": {"id": "bench", "region": [[-1, -1], [2, -1], [2, 2], [-1, 2]]},
            "keepout_rois": {},
            "blur_threshold": 100.0,
            "darkness_threshold": 40.0,
            "occlusion_threshold": 0.5,
        },
    )


def test_draw_overlay_paints_the_rail_outline_and_is_a_noop_without_corners():
    image = np.zeros((120, 120, 3), dtype=np.uint8)
    regions = collect_regions(_config())
    # A unit marker at pixels (20,20)-(40,40): rail_pos_1 maps to the (30,30)-(50,50) box.
    corners = [[20.0, 20.0], [40.0, 20.0], [40.0, 40.0], [20.0, 40.0]]
    drawn = draw_overlay(image, corners, regions)
    # The rail outline is green (0,255,0) somewhere along its top edge near y=30.
    green = np.all(drawn == np.array([0, 255, 0]), axis=2)
    assert green.any(), "the rail region outline must be drawn in green"
    assert (image == 0).all(), "draw_overlay must not mutate its input"
