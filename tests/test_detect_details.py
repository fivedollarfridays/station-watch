"""The torque-stripe detail reader: a pure per-frame read sharing region's gates.

Every test renders a real synthetic clip (:mod:`station_watch.synth.station`), reads
it back through ``cv2.VideoCapture``, finds the marker, and calls ``read_detail`` on
the detail region exactly as ``DetailTracker`` will. The stripe is drawn in the same
marker-unit region the reader samples, so a drawn stripe and the place it is read
are the same bench spot. The unknown-cause gates (out_of_frame/dark/blurred/occluded)
are the *same* helper :mod:`station_watch.detect.regions` uses -- imported from the
one place, never copied.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from station_watch.detect.details import (
    DEFAULT_DETAIL_REGIONS,
    DETAIL_KINDS,
    read_detail,
)
from station_watch.detect.geometry import find_marker_corners, region_to_pixels

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_station import write_synth_station_clip  # noqa: E402

RAIL = {
    "rail_pos_1": [[0.7, 1.9], [1.7, 1.9], [1.7, 2.5], [0.7, 2.5]],
    "rail_pos_2": [[2.5, 1.9], [3.5, 1.9], [3.5, 2.5], [2.5, 2.5]],
}
# A detect dict with the torque-stripe thresholds the reader reads (defaults also work).
DETECT = {
    "blur_threshold": 100.0,
    "darkness_threshold": 40.0,
    "occlusion_threshold": 0.5,
    "details": {"torque_stripe": {"min_fill": 0.15, "hue_range": [20, 40]}},
}
STRIPE_REGION = DEFAULT_DETAIL_REGIONS["rail_pos_1"]["torque_stripe"]


def _frame(tmp_path, spec, tag="f", **render_opts):
    render_opts.setdefault("rail_positions", RAIL)
    path, _truth = write_synth_station_clip(tmp_path / tag, [spec], **render_opts)
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def _poly(frame):
    corners = find_marker_corners(frame, "DICT_4X4_50", 0)
    assert corners is not None
    return region_to_pixels(STRIPE_REGION, corners)


def _present_spec(seed):
    return {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "present"}}}


# --- AC1: present reads present, absent reads absent, across noise seeds ---------


def test_torque_stripe_is_a_registered_kind():
    assert "torque_stripe" in DETAIL_KINDS


def test_present_reads_present_and_absent_reads_absent_across_seeds(tmp_path):
    for seed in range(5):
        present = _frame(
            tmp_path,
            {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "present"}}},
            tag=f"p{seed}",
            seed=seed,
        )
        absent = _frame(
            tmp_path,
            {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "absent"}}},
            tag=f"a{seed}",
            seed=seed,
        )
        rp = read_detail(present, _poly(present), "torque_stripe", DETECT)
        ra = read_detail(absent, _poly(absent), "torque_stripe", DETECT)
        assert rp.state == "present", (seed, rp)
        assert ra.state == "absent", (seed, ra)
        assert rp.cause is None and ra.cause is None
        assert rp.target == "torque_stripe"


def test_stripe_outside_configured_hue_range_reads_absent(tmp_path):
    # The stripe is drawn (yellow, hue ~30), but the configured hue range is a red
    # band matching neither the yellow stripe nor the blue component -- so nothing
    # fills it and the stripe reads absent (its hue is outside the configured range).
    present = _frame(
        tmp_path,
        {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {"torque_stripe": "present"}}},
    )
    detect = {**DETECT, "details": {"torque_stripe": {"min_fill": 0.15, "hue_range": [170, 179]}}}
    r = read_detail(present, _poly(present), "torque_stripe", detect)
    assert r.state == "absent", r


# --- AC2: dim / blurred / occluded read unknown with cause + lower ceiling -------


def _clean_ceiling(tmp_path):
    present = _frame(tmp_path, _present_spec(0), tag="clean")
    return read_detail(present, _poly(present), "torque_stripe", DETECT).confidence_ceiling


def test_dim_blur_occluded_read_unknown_with_cause_and_lower_ceiling(tmp_path):
    clean = _clean_ceiling(tmp_path)

    # Each cause is isolated with a threshold the matching degradation crosses while a
    # clean read does not (the three gates overlap on a solid region otherwise).
    dim = _frame(tmp_path, {**_present_spec(0), "dim": True}, tag="dim")
    detect_dark = {**DETECT, "darkness_threshold": 100.0}
    rd = read_detail(dim, _poly(dim), "torque_stripe", detect_dark)
    assert rd.state == "unknown" and rd.cause == "dark", rd
    assert rd.confidence_ceiling < clean

    blur = _frame(tmp_path, {**_present_spec(0), "blur": True}, tag="blur")
    detect_blur = {**DETECT, "blur_threshold": 1000.0}
    rb = read_detail(blur, _poly(blur), "torque_stripe", detect_blur)
    assert rb.state == "unknown" and rb.cause == "blurred", rb
    assert rb.confidence_ceiling < clean

    # A skin-toned hand blob (with sensor noise, as a real hand carries) over the
    # stripe region occludes it: it is textured enough to clear the blur gate, so the
    # occlusion gate is what reads unknown.
    occ = _frame(tmp_path, _present_spec(0), tag="occ")
    poly = _poly(occ)
    cx, cy = poly.mean(axis=0)
    cv2.circle(occ, (int(cx), int(cy)), 24, (120, 150, 200), -1)
    noise = np.random.default_rng(0).normal(0.0, 8.0, occ.shape)
    occ = np.clip(occ.astype(np.float64) + noise, 0, 255).astype(np.uint8)
    detect_occ = {**DETECT, "occlusion_threshold": 0.3}
    ro = read_detail(occ, poly, "torque_stripe", detect_occ)
    assert ro.state == "unknown" and ro.cause == "occluded", ro
    assert ro.confidence_ceiling < clean
    assert ro.state != "present", "a detail never reads present while occluded"


def test_out_of_frame_region_reads_unknown(tmp_path):
    present = _frame(tmp_path, _present_spec(0))
    # A region that lands partly outside the frame reads unknown/out_of_frame.
    off = np.array([[-5, -5], [-4, -5], [-4, -4], [-5, -4]], dtype=np.int32)
    r = read_detail(present, off, "torque_stripe", DETECT)
    assert r.state == "unknown" and r.cause == "out_of_frame", r


def test_scores_carry_raw_fill_and_quality(tmp_path):
    present = _frame(tmp_path, _present_spec(0))
    r = read_detail(present, _poly(present), "torque_stripe", DETECT)
    assert "fill_fraction" in r.scores
    assert "lap_var" in r.scores and "mean_luma" in r.scores


# --- AC4: the unknown-cause gates are the one shared helper, not duplicated ------


def test_unknown_cause_helper_is_shared_with_regions():
    import station_watch.detect.details as details
    import station_watch.detect.regions as regions

    # Both call sites reach the same helper object -- the gates live in one place.
    assert details.read_region_quality is regions.read_region_quality
