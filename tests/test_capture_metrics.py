"""Unit tests for the per-frame metrics (fingerprint, mean luma, noise score)."""

import numpy as np

from station_watch.capture import fingerprint, mean_luma, noise_score


def _frame(value: int) -> np.ndarray:
    return np.full((16, 16, 3), value, np.uint8)


def test_fingerprint_is_stable_for_identical_bytes():
    assert fingerprint(_frame(100)) == fingerprint(_frame(100))


def test_fingerprint_differs_for_different_bytes():
    assert fingerprint(_frame(100)) != fingerprint(_frame(101))


def test_noise_score_zero_for_byte_identical_frames():
    frame = _frame(120)
    assert noise_score(frame, frame.copy()) == 0.0


def test_noise_score_zero_when_no_previous_frame():
    assert noise_score(_frame(120), None) == 0.0


def test_noise_score_positive_for_differing_frames():
    assert noise_score(_frame(120), _frame(118)) > 0.0


def test_mean_luma_tracks_brightness():
    assert mean_luma(_frame(0)) == 0.0
    assert mean_luma(_frame(255)) > 250.0
    assert mean_luma(_frame(120)) > mean_luma(_frame(20))
