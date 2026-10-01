"""The synthetic station helper writes a real, readable clip and labels every frame.

HF2.2+ (and the harness's synthetic mode) build their proving tests on this: a
panel-bench clip rendered from a per-frame script, returned alongside the ground
truth it drew so the labels come for free. This proves the clip is a real file a
``cv2.VideoCapture`` reads back, the ground truth has exactly one entry per frame,
and the scripted knobs (positions, occlusion, keep-out, motion, dim, blur, hidden
marker) are reflected in the labels. No binary is committed -- it lives in tmp.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402


def _read_back_count(path: Path) -> int:
    cap = cv2.VideoCapture(str(path))
    assert cap.isOpened(), f"cv2.VideoCapture could not open {path}"
    count = 0
    while True:
        ok, _frame = cap.read()
        if not ok:
            break
        count += 1
    cap.release()
    return count


def test_clip_reads_back_through_cv2_and_labels_each_frame(tmp_path):
    script = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "absent"}},
        {"positions": {"rail_pos_1": "not_seated", "rail_pos_2": "present"}, "motion": True},
        {"positions": {"rail_pos_1": "occluded", "rail_pos_2": "present"}},
        {
            "positions": {"rail_pos_1": "present", "rail_pos_2": "present"},
            "keepout": {"zone_press": True},
            "dim": True,
        },
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "blur": True},
        {"hide_marker": True},
    ]

    path, truth = write_synth_station_clip(tmp_path / "station", script)

    assert len(truth) == len(script), "one ground-truth entry per scripted frame"
    assert _read_back_count(path) == len(script), "every frame decodes back through cv2"
    assert [t["frame_id"] for t in truth] == list(range(len(script)))


def test_ground_truth_reflects_the_scripted_knobs(tmp_path):
    script = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "absent"}, "marker_visible": True},
        {
            "positions": {"rail_pos_1": "not_seated", "rail_pos_2": "occluded"},
            "keepout": {"zone_press": True},
            "motion": True,
            "dim": True,
            "blur": True,
        },
        {"hide_marker": True},
    ]

    _path, truth = write_synth_station_clip(tmp_path / "station", script)

    assert truth[0]["positions"] == {"rail_pos_1": "present", "rail_pos_2": "absent"}
    assert truth[0]["marker_visible"] is True
    assert truth[0]["motion"] is False
    assert truth[0]["keepout"] == {"zone_press": False}

    assert truth[1]["positions"] == {"rail_pos_1": "not_seated", "rail_pos_2": "occluded"}
    assert truth[1]["keepout"] == {"zone_press": True}
    assert truth[1]["motion"] is True
    assert truth[1]["dim"] is True
    assert truth[1]["blur"] is True

    assert truth[2]["marker_visible"] is False


def test_rendered_marker_is_detectable_and_regions_land_on_it(tmp_path):
    # The rendered marker is real: the geometry path finds its corners, so the
    # regions a detector will read map onto the same bench the helper drew.
    from station_watch.detect.geometry import find_marker_corners, region_to_pixels

    path, _truth = write_synth_station_clip(
        tmp_path / "station", [{"positions": {"rail_pos_1": "present"}}]
    )
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok

    corners = find_marker_corners(frame, "DICT_4X4_50", 0)
    assert corners is not None
    region = [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]]
    pixels = region_to_pixels(region, corners)
    assert pixels is not None and pixels.shape == (4, 2)
