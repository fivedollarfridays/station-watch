"""Write a synthetic camera clip into a temp dir for Capture tests.

The clip is a *static* scene (a gray backdrop with a drawn ArUco marker) plus
per-frame Gaussian sensor noise, so consecutive frames of a "live" camera differ
even when nothing in the scene moves. The bytes are written with a **lossless**
codec -- FFV1 in an ``.mkv`` when the local OpenCV can open that writer,
otherwise a PNG image sequence that ``cv2.VideoCapture`` reads through the same
file path -- so the injected noise survives decoding and HF1.4 can rely on
consecutive live frames having distinct fingerprints.

Fault knobs match the blind conditions HF1.4 must detect. Each ``*_from`` knob
takes an optional ``*_until`` bound so a clip can go bad and then recover, which
is what the opened-then-cleared blind tests need:

* ``freeze_from`` / ``freeze_until`` -- repeat the *identical bytes* of frame
  ``freeze_from`` while active (a frozen sensor); lossless encoding makes the
  decoded frames byte-identical.
* ``dark_from`` / ``dark_until`` -- the scene goes black while active (sensor blind).
* ``hide_marker_from`` / ``hide_marker_until`` -- the ArUco marker is not drawn
  while active, so the fiducial is missing though the scene stays lit.
* ``marker_move_from`` / ``marker_move_until`` / ``marker_move_px`` -- the drawn
  ArUco marker jumps sideways by K pixels while active (a view shift).
* ``frames`` -- the clip simply stops after this many frames (drop-out).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def _marker_tile(px: int) -> np.ndarray:
    """A single DICT_4X4_50 marker as a BGR tile ``px`` pixels on a side."""
    gray = cv2.aruco.generateImageMarker(_DICT, 0, px)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _base_scene(size: tuple[int, int]) -> np.ndarray:
    """The static backdrop shared by every live frame."""
    height, width = size
    scene = np.full((height, width, 3), 60, np.uint8)
    cv2.rectangle(scene, (8, 8), (width - 8, height - 8), (95, 95, 95), 2)
    return scene


def _compose_frame(
    size: tuple[int, int],
    tile: np.ndarray,
    marker_xy: tuple[int, int],
    *,
    dark: bool,
    draw_marker: bool,
    rng: np.random.Generator,
    noise_sigma: float,
) -> np.ndarray:
    """One live frame: static scene (or black) plus Gaussian sensor noise."""
    if dark:
        frame = np.zeros((size[0], size[1], 3), np.uint8)
    else:
        frame = _base_scene(size)
        if draw_marker:
            x, y = marker_xy
            tile_h, tile_w = tile.shape[:2]
            frame[y : y + tile_h, x : x + tile_w] = tile
    noise = rng.normal(0.0, noise_sigma, frame.shape)
    return np.clip(frame.astype(np.float64) + noise, 0, 255).astype(np.uint8)


def _write_png_sequence(dir_path: Path, seq: list[np.ndarray]) -> Path:
    """Fallback: write a PNG sequence and return the ``%05d`` pattern path."""
    for i, frame in enumerate(seq):
        cv2.imwrite(str(dir_path / f"frame_{i:05d}.png"), frame)
    return dir_path / "frame_%05d.png"


def _write_frames(dir_path: Path, seq: list[np.ndarray], fps: float) -> Path:
    """Write ``seq`` losslessly, preferring FFV1 ``.mkv`` over a PNG sequence."""
    height, width = seq[0].shape[:2]
    mkv = dir_path / "clip.mkv"
    writer = cv2.VideoWriter(str(mkv), cv2.VideoWriter_fourcc(*"FFV1"), fps, (width, height), True)
    if writer.isOpened():
        for frame in seq:
            writer.write(frame)
        writer.release()
        return mkv
    writer.release()
    return _write_png_sequence(dir_path, seq)


def _in_window(i: int, start: int | None, until: int | None) -> bool:
    """True when frame ``i`` falls in ``[start, until)`` (``until`` open-ended if None)."""
    return start is not None and i >= start and (until is None or i < until)


def write_synth_clip(
    dir_path: Path | str,
    *,
    frames: int = 30,
    size: tuple[int, int] = (120, 120),
    fps: float = 10.0,
    noise_sigma: float = 8.0,
    seed: int = 0,
    marker_px: int = 40,
    marker_xy: tuple[int, int] = (20, 20),
    freeze_from: int | None = None,
    freeze_until: int | None = None,
    dark_from: int | None = None,
    dark_until: int | None = None,
    hide_marker_from: int | None = None,
    hide_marker_until: int | None = None,
    marker_move_from: int | None = None,
    marker_move_until: int | None = None,
    marker_move_px: int = 0,
) -> Path:
    """Generate a synthetic clip under ``dir_path`` and return its capture path.

    The returned path is what a real ``cv2.VideoCapture`` opens (an ``.mkv`` file
    or a PNG-sequence pattern). ``dir_path`` is created if needed; pass a path
    under a test temp dir so nothing is written outside it.
    """
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tile = _marker_tile(marker_px)
    seq: list[np.ndarray] = []
    for i in range(frames):
        if freeze_from is not None and _in_window(i, freeze_from + 1, freeze_until) and seq:
            seq.append(seq[freeze_from])  # identical bytes -> frozen sensor
            continue
        shifted = _in_window(i, marker_move_from, marker_move_until)
        marker_x = marker_xy[0] + (marker_move_px if shifted else 0)
        frame = _compose_frame(
            size,
            tile,
            (marker_x, marker_xy[1]),
            dark=_in_window(i, dark_from, dark_until),
            draw_marker=not _in_window(i, hide_marker_from, hide_marker_until),
            rng=rng,
            noise_sigma=noise_sigma,
        )
        seq.append(frame)
    return _write_frames(dir_path, seq, fps)
