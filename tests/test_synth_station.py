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
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402


def _first_frame(path):
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


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


# --- HF3.15: the details knob draws a stripe, labels it, and is a no-op when off ---


def test_a_frame_without_details_is_byte_identical_to_an_all_absent_detail_frame(tmp_path):
    # A frame with no `details` key, and one whose only detail is "absent", draw the
    # same pixels for the same seed -- the detail machinery adds nothing when nothing
    # is present, so every existing (detail-free) clip renders exactly as before.
    base = {"positions": {"rail_pos_1": "present"}}
    absent = {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "absent"}}}
    present = {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "present"}}}

    p_base, _ = write_synth_station_clip(tmp_path / "base", [base], seed=3)
    p_absent, _ = write_synth_station_clip(tmp_path / "absent", [absent], seed=3)
    p_present, _ = write_synth_station_clip(tmp_path / "present", [present], seed=3)

    assert np.array_equal(_first_frame(p_base), _first_frame(p_absent)), "absent detail draws nothing"
    assert not np.array_equal(_first_frame(p_base), _first_frame(p_present)), "a stripe changes pixels"


def test_ground_truth_carries_the_drawn_details(tmp_path):
    script = [
        {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "present"}}},
        {"positions": {"rail_pos_1": "present"}},
    ]
    _path, truth = write_synth_station_clip(tmp_path / "d", script)
    assert truth[0]["details"] == {"rail_pos_1": {"torque_stripe": "present"}}
    assert truth[1]["details"] == {}, "a frame with no details key reports no details drawn"


# --- HF3.16: adding the label and ferrule kinds leaves detail-free frames unchanged ---

# SHA-256 of a detail-free clip's concatenated frame bytes, captured on the pre-HF3.16
# renderer. HF3.16 adds the label/ferrule colours, the ferrule ridge draw and the label/
# ferrule default regions; none of that touches a frame whose spec has no `details`, so
# these hashes must stay fixed -- every existing clip renders byte-for-byte as before.
_DETAIL_FREE_HASHES = {
    0: "dea5bbe8a33b094129dafc9c8bcdbf8c48ecee8e1916bac7435b0f04d06422f9",
    1: "13191becef5c069f0579e682fa91539c3e155c9dd24950de2d3917063808032b",
    2: "c9939ba9327c0a66192b92a9508b3debd6aeb4cd6455e7880b4b1a0e2f9f370b",
}


def test_detail_free_frames_are_byte_identical_to_before_hf3_16(tmp_path):
    import hashlib

    rail = {
        "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
        "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
    }
    script = [
        {"positions": {"rail_pos_1": "present", "rail_pos_2": "present"}, "motion": True},
        {"positions": {"rail_pos_1": "absent", "rail_pos_2": "present"}},
        {
            "positions": {"rail_pos_1": "occluded", "rail_pos_2": "present"},
            "keepout": {"zone_press": True},
        },
    ]
    for seed, expected in _DETAIL_FREE_HASHES.items():
        path, _ = write_synth_station_clip(
            tmp_path / f"s{seed}", script, rail_positions=rail, seed=seed
        )
        cap = cv2.VideoCapture(str(path))
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frames.append(frame)
        cap.release()
        digest = hashlib.sha256(b"".join(f.tobytes() for f in frames)).hexdigest()
        assert digest == expected, f"seed {seed}: detail-free rendering changed ({digest})"
