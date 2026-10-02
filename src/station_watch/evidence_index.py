"""The read side of the evidence store: resolve a cited frame id to its frame.

HF3.5 consumes this: :meth:`EvidenceIndex.load` reads one run's ``index.jsonl``
(frames that were kept) and ``misses.jsonl`` (why a frame was not), so
:meth:`EvidenceIndex.lookup` can return the kept :class:`EvidenceFrame` or a
:class:`MissingEvidence` that *names the reason* -- never silently nothing, and
never a neighbouring frame. :func:`frame_from_clip` is the recorded-file path: it
re-reads one frame from the clip and returns it only if it still hashes to the
Log's fingerprint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from station_watch.capture.metrics import fingerprint as _fingerprint

INDEX_NAME = "index.jsonl"
MISSES_NAME = "misses.jsonl"

# The MissingEvidence vocabulary (shared with the store's miss records).
NOT_CAPTURED = "not_captured"
PRUNED = "pruned"
DROPPED = "dropped"
WRITE_FAILED = "write_failed"
FINGERPRINT_MISMATCH = "fingerprint_mismatch"
NOT_IN_CLIP = "not_in_clip"


@dataclass(frozen=True)
class EvidenceFrame:
    """A kept evidence thumbnail and the geometry needed to read it (HF3.5)."""

    frame_id: int
    ts: str
    fingerprint: str
    path: Path  # absolute path to the JPEG on disk
    width: int
    height: int
    scale: float  # thumbnail px / full-frame px
    corners: list[list[float]] | None  # four [x, y] in thumbnail px, or None


@dataclass(frozen=True)
class MissingEvidence:
    """Why a frame id has no readable evidence frame."""

    frame_id: int
    reason: str


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class EvidenceIndex:
    """Resolve a cited frame id to its kept frame or the reason it is absent."""

    def __init__(self, run_dir: Path, written: dict[int, dict], misses: dict[int, str]) -> None:
        self._dir = run_dir
        self._written = written
        self._misses = misses

    @classmethod
    def load(cls, evidence_dir: Path | str, run_id: str) -> EvidenceIndex:
        """Load the index for one run; an absent dir yields an all-``not_captured`` index."""
        run_dir = Path(evidence_dir) / run_id
        written = {e["frame_id"]: e for e in _read_jsonl(run_dir / INDEX_NAME)}
        misses = {e["frame_id"]: e["reason"] for e in _read_jsonl(run_dir / MISSES_NAME)}
        return cls(run_dir, written, misses)

    def lookup(self, frame_id: int) -> EvidenceFrame | MissingEvidence:
        """The kept :class:`EvidenceFrame`, or a :class:`MissingEvidence` reason.

        A recorded miss (pruned / dropped / write_failed) wins over an index line,
        because it is always the later event for that frame id.
        """
        if frame_id in self._misses:
            return MissingEvidence(frame_id, self._misses[frame_id])
        entry = self._written.get(frame_id)
        if entry is None:
            return MissingEvidence(frame_id, NOT_CAPTURED)
        path = (self._dir / entry["path"]).resolve()
        if not path.exists():
            return MissingEvidence(frame_id, PRUNED)
        return EvidenceFrame(
            frame_id=frame_id,
            ts=entry["ts"],
            fingerprint=entry["fingerprint"],
            path=path,
            width=entry["width"],
            height=entry["height"],
            scale=entry["scale"],
            corners=entry["corners"],
        )


def frame_from_clip(
    clip_path: Path | str, frame_id: int, fingerprint: str
) -> np.ndarray | MissingEvidence:
    """Re-read one frame from a recorded clip, returned only if its fingerprint matches.

    For a recorded-file session the real pixels live in the clip; this re-reads
    frame ``frame_id`` and hands it back only when it still hashes to ``fingerprint``
    (the Log's ``FrameRecord`` fingerprint), so a re-encoded or wrong clip can never
    pass off a different frame. Returns :class:`MissingEvidence` with reason
    ``not_in_clip`` (the clip is shorter) or ``fingerprint_mismatch`` otherwise.
    """
    cap = cv2.VideoCapture(str(clip_path))
    try:
        frame = None
        for _ in range(frame_id + 1):
            ok, frame = cap.read()
            if not ok or frame is None:
                return MissingEvidence(frame_id, NOT_IN_CLIP)
    finally:
        cap.release()
    if _fingerprint(frame) != fingerprint:
        return MissingEvidence(frame_id, FINGERPRINT_MISMATCH)
    return frame


__all__ = [
    "EvidenceIndex",
    "EvidenceFrame",
    "MissingEvidence",
    "frame_from_clip",
    "INDEX_NAME",
    "MISSES_NAME",
    "NOT_CAPTURED",
    "PRUNED",
    "DROPPED",
    "WRITE_FAILED",
]
