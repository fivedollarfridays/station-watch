"""Unit tests for the evidence store, index, and clip re-reader (HF3.4).

These exercise :mod:`station_watch.evidence` directly -- the write/queue/retention
machinery and the read-side :class:`EvidenceIndex` / :func:`frame_from_clip` --
away from the live Capture wiring, which is proven end to end in
:mod:`tests.test_evidence_e2e`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from station_watch.capture.metrics import fingerprint as compute_fingerprint
from station_watch.evidence import (
    DEFAULT_MAX_FILES,
    DEFAULT_THUMB_WIDTH,
    EvidenceFrame,
    EvidenceIndex,
    EvidenceSettings,
    EvidenceStore,
    MissingEvidence,
    frame_from_clip,
)

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import _write_frames  # noqa: E402

RUN = "run-abcd"


def _frame(seed: int, *, h: int = 120, w: int = 160) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def _store(tmp_path, **over) -> EvidenceStore:
    return EvidenceStore(tmp_path / "evidence", RUN, **over)


def _note(store, frame, frame_id, *, ts="t", observation):
    """Offer one frame to the store (fingerprint computed from the pixels)."""
    store.note_frame(
        frame, frame_id, ts, compute_fingerprint(frame), None, had_observation=observation
    )


def test_observation_frame_is_written_and_resolves_with_matching_fingerprint(tmp_path):
    frame = _frame(1)
    store = _store(tmp_path)
    _note(store, frame, 7, ts="2026-01-01T00:00:00.000000+00:00", observation=True)
    store.close()

    index = EvidenceIndex.load(tmp_path / "evidence", RUN)
    got = index.lookup(7)
    assert isinstance(got, EvidenceFrame)
    assert got.frame_id == 7
    assert got.fingerprint == compute_fingerprint(frame)
    assert got.path.is_absolute() and got.path.exists()
    assert got.width <= DEFAULT_THUMB_WIDTH


def test_a_frame_with_no_observation_is_not_written(tmp_path):
    frame = _frame(2)
    store = _store(tmp_path)
    store.note_frame(frame, 3, "t", compute_fingerprint(frame), None, had_observation=False)
    store.close()

    index = EvidenceIndex.load(tmp_path / "evidence", RUN)
    got = index.lookup(3)
    assert got == MissingEvidence(3, "not_captured")


def test_downscale_scales_corners_into_thumbnail_pixels(tmp_path):
    frame = _frame(3, h=480, w=640)  # 640 > default thumb width 320 -> scale 0.5
    corners = np.array([[10, 20], [110, 20], [110, 120], [10, 120]], dtype=np.float32)
    store = _store(tmp_path, thumb_width=320)
    store.note_frame(frame, 1, "t", compute_fingerprint(frame), corners, had_observation=True)
    store.close()

    frame_ev = EvidenceIndex.load(tmp_path / "evidence", RUN).lookup(1)
    assert isinstance(frame_ev, EvidenceFrame)
    assert frame_ev.scale == 0.5
    assert frame_ev.width == 320
    assert frame_ev.corners == [[5.0, 10.0], [55.0, 10.0], [55.0, 60.0], [5.0, 60.0]]


def test_small_frame_is_not_upscaled_and_corners_unscaled(tmp_path):
    frame = _frame(4, h=120, w=160)  # below thumb width -> scale 1.0
    corners = np.array([[1, 2], [3, 2], [3, 4], [1, 4]], dtype=np.float32)
    store = _store(tmp_path, thumb_width=320)
    store.note_frame(frame, 0, "t", compute_fingerprint(frame), corners, had_observation=True)
    store.close()

    frame_ev = EvidenceIndex.load(tmp_path / "evidence", RUN).lookup(0)
    assert isinstance(frame_ev, EvidenceFrame)
    assert frame_ev.scale == 1.0
    assert frame_ev.width == 160 and frame_ev.height == 120
    assert frame_ev.corners == [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]]


def test_last_good_before_blind_is_written_from_the_ring(tmp_path):
    store = _store(tmp_path)
    frames = {}
    for fid in range(5):
        frames[fid] = _frame(100 + fid)
        _note(store, frames[fid], fid, observation=False)
    # No observations were emitted, but a blind opens citing frame 2 as last good.
    store.note_blind_open(2)
    store.close()

    frame_ev = EvidenceIndex.load(tmp_path / "evidence", RUN).lookup(2)
    assert isinstance(frame_ev, EvidenceFrame)
    # The stored fingerprint is recomputed from the kept pixels: it equals the Log's.
    assert frame_ev.fingerprint == compute_fingerprint(frames[2])


def test_note_blind_open_none_writes_nothing(tmp_path):
    store = _store(tmp_path)
    frame = _frame(5)
    store.note_frame(frame, 0, "t", compute_fingerprint(frame), None, had_observation=False)
    store.note_blind_open(None)
    store.close()
    index = EvidenceIndex.load(tmp_path / "evidence", RUN)
    assert index.lookup(0) == MissingEvidence(0, "not_captured")


def test_repeat_citations_dedupe_to_one_file(tmp_path):
    frame = _frame(6)
    store = _store(tmp_path)
    fp = compute_fingerprint(frame)
    store.note_frame(frame, 9, "t", fp, None, had_observation=True)
    store.note_blind_open(9)  # cite the same frame again
    store.close()
    assert store.written == 1
    files = list((tmp_path / "evidence" / RUN).glob("frame_*.jpg"))
    assert len(files) == 1


def test_a_writer_that_always_raises_counts_and_reports_once(tmp_path, capsys):
    def boom(_thumbnail):
        raise RuntimeError("disk full")

    reports = []
    store = EvidenceStore(
        tmp_path / "evidence", RUN, encode=boom, report=reports.append
    )
    for fid in range(4):
        frame = _frame(fid)
        store.note_frame(frame, fid, "t", compute_fingerprint(frame), None, had_observation=True)
    store.close()

    assert store.write_errors == 4
    assert store.written == 0
    assert len(reports) == 1, "the failure is reported once, not once per frame"
    index = EvidenceIndex.load(tmp_path / "evidence", RUN)
    for fid in range(4):
        assert index.lookup(fid) == MissingEvidence(fid, "write_failed")


def test_a_full_queue_drops_the_frame_and_counts_it(tmp_path):
    # A size-1 queue with a writer blocked on a slow encode forces a drop.
    import threading

    release = threading.Event()

    def slow(thumbnail):
        release.wait(5.0)
        ok, buf = __import__("cv2").imencode(".jpg", thumbnail)
        return buf.tobytes()

    store = EvidenceStore(tmp_path / "evidence", RUN, queue_size=1, encode=slow)
    # First submission is picked up by the writer (and blocks); the next few fill
    # the single queue slot and then overflow -> dropped.
    dropped_ids = []
    for fid in range(6):
        frame = _frame(fid)
        store.note_frame(frame, fid, "t", compute_fingerprint(frame), None, had_observation=True)
    release.set()
    store.close()

    assert store.dropped >= 1, "at least one frame overflowed the bounded queue"
    index = EvidenceIndex.load(tmp_path / "evidence", RUN)
    reasons = {fid: index.lookup(fid) for fid in range(6)}
    dropped_ids = [fid for fid, r in reasons.items() if isinstance(r, MissingEvidence)]
    assert dropped_ids, "a dropped frame reports 'dropped', never a neighbour frame"
    for fid in dropped_ids:
        assert reasons[fid].reason == "dropped"


def test_retention_prunes_oldest_first_and_lookup_says_pruned(tmp_path):
    store = _store(tmp_path, max_files=2)
    frames = {}
    for fid in range(5):
        frames[fid] = _frame(200 + fid)
        _note(store, frames[fid], fid, observation=True)
    store.close()

    index = EvidenceIndex.load(tmp_path / "evidence", RUN)
    # Only the two newest survive; the three oldest are pruned (never a neighbour).
    assert isinstance(index.lookup(3), EvidenceFrame)
    assert isinstance(index.lookup(4), EvidenceFrame)
    for fid in (0, 1, 2):
        got = index.lookup(fid)
        assert isinstance(got, MissingEvidence) and got.reason == "pruned", got
    # On-disk file count respects the cap.
    assert len(list((tmp_path / "evidence" / RUN).glob("frame_*.jpg"))) == 2


def test_load_on_a_missing_dir_is_all_not_captured(tmp_path):
    index = EvidenceIndex.load(tmp_path / "nope", "run-x")
    assert index.lookup(0) == MissingEvidence(0, "not_captured")


def test_frame_from_clip_matches_fingerprint_and_rejects_a_wrong_one(tmp_path):
    frames = [_frame(300 + i, h=80, w=120) for i in range(4)]
    clip_dir = tmp_path / "clip"
    clip_dir.mkdir()
    clip = _write_frames(clip_dir, frames, 10.0)

    got = frame_from_clip(clip, 2, compute_fingerprint(frames[2]))
    assert isinstance(got, np.ndarray)
    assert compute_fingerprint(got) == compute_fingerprint(frames[2])

    wrong = frame_from_clip(clip, 2, compute_fingerprint(frames[0]))
    assert wrong == MissingEvidence(2, "fingerprint_mismatch")

    past_end = frame_from_clip(clip, 99, compute_fingerprint(frames[0]))
    assert past_end == MissingEvidence(99, "not_in_clip")


def test_evidence_settings_reads_section_with_defaults():
    assert EvidenceSettings.from_config_mapping({}) == EvidenceSettings(
        DEFAULT_THUMB_WIDTH, DEFAULT_MAX_FILES
    )
    assert EvidenceSettings.from_config_mapping(None) == EvidenceSettings()
    got = EvidenceSettings.from_config_mapping({"evidence": {"thumb_width": 128, "max_files": 10}})
    assert got == EvidenceSettings(thumb_width=128, max_files=10)
