"""The label and ferrule detail readers (HF3.16): pure per-frame reads, shared gates.

Mirrors :mod:`tests.test_detect_details` (the torque-stripe reader) for the two kinds
HF3.16 adds. Every test renders a real synthetic clip, reads it back through
``cv2.VideoCapture``, finds the marker, and calls ``read_detail`` on the kind's default
region exactly as ``DetailTracker`` will -- so a drawn label/ferrule and the place it is
read are the same bench spot. The unknown-cause gates (out_of_frame/dark/blurred/
occluded) are the *same* helper :mod:`station_watch.detect.regions` uses, so a dim,
blurred or hand-covered detail reads ``unknown`` with the matching cause and a lower
confidence ceiling than a clean read.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

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
# The default region-quality cutoffs; each unknown test raises exactly the one gate it
# isolates (the three gates overlap on a solid region otherwise), as the torque tests do.
DETECT = {"blur_threshold": 100.0, "darkness_threshold": 40.0, "occlusion_threshold": 0.5}

NEW_KINDS = ("label", "ferrule")


def _frame(tmp_path, spec, tag="f", **render_opts):
    render_opts.setdefault("rail_positions", RAIL)
    path, _truth = write_synth_station_clip(tmp_path / tag, [spec], **render_opts)
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def _poly(frame, kind):
    corners = find_marker_corners(frame, "DICT_4X4_50", 0)
    assert corners is not None
    return region_to_pixels(DEFAULT_DETAIL_REGIONS["rail_pos_1"][kind], corners)


def _spec(kind, state):
    return {"positions": {"rail_pos_1": "present"}, "details": {"rail_pos_1": {kind: state}}}


# --- the registry: both kinds live, in the order HF3.17 consumes --------------------


def test_detail_kinds_are_label_ferrule_torque_stripe_in_order():
    assert DETAIL_KINDS == ("label", "ferrule", "torque_stripe")


# --- AC1: present reads present, absent reads absent, across >= 5 noise seeds --------


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_present_reads_present_and_absent_reads_absent_across_seeds(tmp_path, kind):
    for seed in range(5):
        present = _frame(tmp_path, _spec(kind, "present"), tag=f"{kind}p{seed}", seed=seed)
        absent = _frame(tmp_path, _spec(kind, "absent"), tag=f"{kind}a{seed}", seed=seed)
        rp = read_detail(present, _poly(present, kind), kind, DETECT)
        ra = read_detail(absent, _poly(absent, kind), kind, DETECT)
        assert rp.state == "present", (kind, seed, rp)
        assert ra.state == "absent", (kind, seed, ra)
        assert rp.cause is None and ra.cause is None
        assert rp.target == kind


# --- AC2: dim / blurred / occluded read unknown with the cause + a lower ceiling -----


def _clean_ceiling(tmp_path, kind):
    present = _frame(tmp_path, _spec(kind, "present"), tag=f"{kind}clean")
    return read_detail(present, _poly(present, kind), kind, DETECT).confidence_ceiling


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_dim_reads_unknown_dark_with_lower_ceiling(tmp_path, kind):
    clean = _clean_ceiling(tmp_path, kind)
    dim = _frame(tmp_path, {**_spec(kind, "present"), "dim": True}, tag=f"{kind}dim")
    rd = read_detail(dim, _poly(dim, kind), kind, {**DETECT, "darkness_threshold": 120.0})
    assert rd.state == "unknown" and rd.cause == "dark", rd
    assert rd.confidence_ceiling < clean


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_blurred_reads_unknown_blurred_with_lower_ceiling(tmp_path, kind):
    clean = _clean_ceiling(tmp_path, kind)
    blur = _frame(tmp_path, {**_spec(kind, "present"), "blur": True}, tag=f"{kind}blur")
    rb = read_detail(blur, _poly(blur, kind), kind, {**DETECT, "blur_threshold": 1000.0})
    assert rb.state == "unknown" and rb.cause == "blurred", rb
    assert rb.confidence_ceiling < clean


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_hand_occluded_reads_unknown_occluded_with_lower_ceiling(tmp_path, kind):
    clean = _clean_ceiling(tmp_path, kind)
    # A skin-toned hand blob (with sensor noise, as a real hand carries) over the detail
    # region occludes it: textured enough to clear the blur gate, so occlusion reads it.
    occ = _frame(tmp_path, _spec(kind, "present"), tag=f"{kind}occ")
    poly = _poly(occ, kind)
    cx, cy = poly.mean(axis=0)
    cv2.circle(occ, (int(cx), int(cy)), 24, (120, 150, 200), -1)
    noise = np.random.default_rng(0).normal(0.0, 8.0, occ.shape)
    occ = np.clip(occ.astype(np.float64) + noise, 0, 255).astype(np.uint8)
    ro = read_detail(occ, poly, kind, {**DETECT, "occlusion_threshold": 0.3})
    assert ro.state == "unknown" and ro.cause == "occluded", ro
    assert ro.confidence_ceiling < clean
    assert ro.state != "present", "a detail never reads present while occluded"


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_out_of_frame_region_reads_unknown(tmp_path, kind):
    present = _frame(tmp_path, _spec(kind, "present"), tag=f"{kind}oof")
    off = np.array([[-5, -5], [-4, -5], [-4, -4], [-5, -4]], dtype=np.int32)
    r = read_detail(present, off, kind, DETECT)
    assert r.state == "unknown" and r.cause == "out_of_frame", r


# --- K4: each reading carries its raw fill scores plus the shared quality scores -----


def test_label_scores_carry_fill_and_quality(tmp_path):
    present = _frame(tmp_path, _spec("label", "present"))
    r = read_detail(present, _poly(present, "label"), "label", DETECT)
    assert "fill_fraction" in r.scores
    assert "lap_var" in r.scores and "mean_luma" in r.scores


def test_ferrule_scores_carry_fill_edge_density_and_quality(tmp_path):
    present = _frame(tmp_path, _spec("ferrule", "present"))
    r = read_detail(present, _poly(present, "ferrule"), "ferrule", DETECT)
    assert "fill_fraction" in r.scores and "edge_density" in r.scores
    assert "lap_var" in r.scores and "mean_luma" in r.scores
