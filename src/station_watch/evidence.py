"""Evidence frames: a durable thumbnail of every frame a flag can cite.

The Log stores frame *metadata* (id, ts, fingerprint, metrics) but never pixels,
so once a run moves on the frames a fault cites are gone. :class:`EvidenceStore`
keeps them: owned by Capture's detect hook, it writes a downscaled JPEG for

* every frame Detect emitted at least one ``Observation`` for (fault citations
  are observation frame ids, so this keeps every *citable* frame), and
* the last good frame before each blind record opens
  (:attr:`~station_watch.records.BlindRecord.last_good_frame_id`),

which is far fewer frames than the stream. Each written frame gets one JSON line
in ``<evidence_dir>/<run_id>/index.jsonl``; frames that could not be kept (queue
full, a write that raised, a retention prune) get one line in ``misses.jsonl`` so
a later :meth:`EvidenceIndex.lookup` can say *why* a cited frame is absent rather
than silently returning nothing (or, worse, the wrong frame).

Writes go through a bounded queue drained by a single writer thread: a full queue
drops the frame and counts it, a write that raises is counted and reported once,
and neither is ever raised back into Capture (K5, K10) -- a run with a broken
evidence writer records exactly the verdicts, alarms and cycles it would with
evidence off.

:meth:`EvidenceIndex.load` / :meth:`EvidenceIndex.lookup` are the read side HF3.5
consumes; :func:`frame_from_clip` re-reads a single frame from a recorded clip and
returns it only when its fingerprint still matches the Log's.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from station_watch.capture.metrics import fingerprint as _fingerprint

# Documented defaults for the optional ``evidence:`` config section.
DEFAULT_THUMB_WIDTH = 320
DEFAULT_MAX_FILES = 2000
# Capture-side buffering. The queue bounds how many pending writes can pile up
# before frames are dropped; the ring retains recent frames so the last good one
# before a blind opens is still available to write.
DEFAULT_QUEUE_SIZE = 256
DEFAULT_RING_SIZE = 64

INDEX_NAME = "index.jsonl"
MISSES_NAME = "misses.jsonl"

# lookup / frame_from_clip reasons (the MissingEvidence vocabulary).
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


@dataclass(frozen=True)
class EvidenceSettings:
    """Retention knobs from the optional ``evidence:`` config section."""

    thumb_width: int = DEFAULT_THUMB_WIDTH
    max_files: int = DEFAULT_MAX_FILES

    @classmethod
    def from_config_mapping(cls, data: dict[str, Any] | None) -> EvidenceSettings:
        """Read ``evidence.thumb_width`` / ``evidence.max_files``; defaults when absent."""
        section = (data or {}).get("evidence") or {}
        return cls(
            thumb_width=int(section.get("thumb_width", DEFAULT_THUMB_WIDTH)),
            max_files=int(section.get("max_files", DEFAULT_MAX_FILES)),
        )


def _default_report(message: str) -> None:
    """One operator-facing line on stderr, flushed at once (matches Capture's)."""
    print(f"station-watch: {message}", file=sys.stderr, flush=True)


def _encode_jpeg(thumbnail: np.ndarray) -> bytes:
    """JPEG-encode an already-downscaled BGR thumbnail."""
    ok, buffer = cv2.imencode(".jpg", thumbnail)
    if not ok:
        raise ValueError("cv2.imencode failed to encode evidence thumbnail")
    return buffer.tobytes()


def _downscale(frame: np.ndarray, thumb_width: int) -> tuple[np.ndarray, float]:
    """Downscale ``frame`` to at most ``thumb_width`` wide; never upscale.

    Returns the thumbnail and the scale (thumbnail px / full-frame px) so a marker
    polygon in full-frame pixels maps into the thumbnail by multiplication.
    """
    height, width = frame.shape[:2]
    if width <= thumb_width:
        return frame, 1.0
    scale = thumb_width / width
    target = (thumb_width, max(1, round(height * scale)))
    return cv2.resize(frame, target, interpolation=cv2.INTER_AREA), scale


def _scaled_corners(corners, scale: float) -> list[list[float]] | None:
    """The four marker corners scaled into thumbnail pixels, or ``None``."""
    if corners is None:
        return None
    points = np.asarray(corners, dtype=np.float32).reshape(4, 2) * scale
    return [[float(x), float(y)] for x, y in points]


@dataclass
class _Job:
    frame_id: int
    frame: np.ndarray
    ts: str
    fingerprint: str
    corners: Any


class EvidenceStore:
    """Writes evidence thumbnails off the Capture thread; never raises into it."""

    def __init__(
        self,
        evidence_dir: Path | str,
        run_id: str,
        *,
        thumb_width: int = DEFAULT_THUMB_WIDTH,
        max_files: int = DEFAULT_MAX_FILES,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        ring_size: int = DEFAULT_RING_SIZE,
        encode: Callable[[np.ndarray], bytes] = _encode_jpeg,
        report: Callable[[str], None] = _default_report,
    ) -> None:
        self._dir = Path(evidence_dir) / run_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._thumb_width = thumb_width
        self._max_files = max(1, max_files)
        self._encode = encode
        self._report = report
        self._ring_size = max(1, ring_size)
        self._ring: OrderedDict[int, np.ndarray] = OrderedDict()
        self._submitted: set[int] = set()
        self._lock = threading.Lock()
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=queue_size)
        self._index = (self._dir / INDEX_NAME).open("a", encoding="utf-8")
        self._misses = (self._dir / MISSES_NAME).open("a", encoding="utf-8")
        self._written: OrderedDict[int, str] = OrderedDict()  # frame_id -> filename
        self._error_reported = False
        # Counters, readable after close() for metrics/tests.
        self.dropped = 0
        self.write_errors = 0
        self.written = 0
        self.pruned = 0
        self._stop = False
        self._thread = threading.Thread(
            target=self._writer_loop, name="station-watch-evidence", daemon=True
        )
        self._thread.start()

    # --- Capture-thread facing API (must never raise) ---------------------------

    def note_frame(self, frame, frame_id: int, ts: str, fingerprint: str, corners, *, had_observation: bool) -> None:
        """Record one frame: keep it for last-good lookups, write it if cited.

        Every frame is cached (any frame may later be a blind's last good frame);
        a frame Detect emitted at least one ``Observation`` for is written now.
        """
        with self._lock:
            self._ring[frame_id] = frame.copy()
            self._ring.move_to_end(frame_id)
            while len(self._ring) > self._ring_size:
                self._ring.popitem(last=False)
            if had_observation:
                self._submit_locked(frame_id, ts, fingerprint, corners)

    def note_blind_open(self, last_good_frame_id: int | None) -> None:
        """Write the last good frame before a blind record opened, if we still have it."""
        if last_good_frame_id is None:
            return
        with self._lock:
            self._submit_locked(last_good_frame_id, None, None, None)

    def _submit_locked(self, frame_id: int, ts, fingerprint, corners) -> None:
        """Enqueue ``frame_id`` for writing once; dedupe repeat citations."""
        if frame_id in self._submitted:
            return
        frame = self._ring.get(frame_id)
        if frame is None:
            # Aged out of the ring before we could write it: count it as dropped so
            # lookup never silently misses (and never returns a neighbour).
            self._submitted.add(frame_id)
            self.dropped += 1
            self._record_miss(frame_id, DROPPED)
            return
        if ts is None:
            ts, fingerprint = self._metadata_for(frame_id, frame)
        job = _Job(frame_id, frame, ts, fingerprint, corners)
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self.dropped += 1
            self._record_miss(frame_id, DROPPED)
            return
        self._submitted.add(frame_id)

    def _metadata_for(self, frame_id: int, frame) -> tuple[str, str]:
        """ts / fingerprint for a last-good frame submitted without an Observation.

        The last good frame may never have produced an observation, so Capture has
        no record metadata to hand us; the fingerprint is recomputed from the kept
        pixels (identical to the Log's :class:`FrameRecord`) and ts is left empty.
        """
        return "", _fingerprint(frame)

    # --- Writer thread ----------------------------------------------------------

    def _writer_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            self._write_one(job)

    def _write_one(self, job: _Job) -> None:
        try:
            thumbnail, scale = _downscale(job.frame, self._thumb_width)
            data = self._encode(thumbnail)
            name = f"frame_{job.frame_id:08d}.jpg"
            (self._dir / name).write_bytes(data)
            height, width = thumbnail.shape[:2]
            self._append_index(job, name, width, height, scale)
            self.written += 1
            self._prune()
        except Exception as exc:  # a write error is counted and reported once (K5)
            self.write_errors += 1
            self._record_miss(job.frame_id, WRITE_FAILED)
            if not self._error_reported:
                self._error_reported = True
                self._report(f"evidence write failed for frame {job.frame_id}: {exc!r}")

    def _append_index(self, job: _Job, name: str, width: int, height: int, scale: float) -> None:
        entry = {
            "frame_id": job.frame_id,
            "ts": job.ts,
            "fingerprint": job.fingerprint,
            "path": name,
            "width": width,
            "height": height,
            "scale": scale,
            "corners": _scaled_corners(job.corners, scale),
        }
        self._index.write(json.dumps(entry) + "\n")
        self._index.flush()
        self._written[job.frame_id] = name

    def _prune(self) -> None:
        """Enforce ``max_files``: delete the oldest kept frames, oldest first."""
        while len(self._written) > self._max_files:
            frame_id, name = self._written.popitem(last=False)
            (self._dir / name).unlink(missing_ok=True)
            self.pruned += 1
            self._record_miss(frame_id, PRUNED)

    def _record_miss(self, frame_id: int, reason: str) -> None:
        """Persist why a frame id has no readable evidence (last writer wins)."""
        self._misses.write(json.dumps({"frame_id": frame_id, "reason": reason}) + "\n")
        self._misses.flush()

    def close(self) -> None:
        """Drain the queue, stop the writer thread, and close the index files."""
        if self._stop:
            return
        self._stop = True
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        self._index.close()
        self._misses.close()


class EvidenceIndex:
    """The read side: resolve a cited frame id to its kept frame or a reason."""

    def __init__(self, run_dir: Path, written: dict[int, dict], misses: dict[int, str]) -> None:
        self._dir = run_dir
        self._written = written
        self._misses = misses

    @classmethod
    def load(cls, evidence_dir: Path | str, run_id: str) -> EvidenceIndex:
        """Load the index for one run; an absent dir yields an all-``not_captured`` index."""
        run_dir = Path(evidence_dir) / run_id
        written = {entry["frame_id"]: entry for entry in _read_jsonl(run_dir / INDEX_NAME)}
        misses = {entry["frame_id"]: entry["reason"] for entry in _read_jsonl(run_dir / MISSES_NAME)}
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


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def frame_from_clip(clip_path: Path | str, frame_id: int, fingerprint: str) -> np.ndarray | MissingEvidence:
    """Re-read one frame from a recorded clip, returned only if its fingerprint matches.

    For a recorded-file session the real pixels live in the clip; this re-reads
    frame ``frame_id`` and hands it back only when it still hashes to ``fingerprint``
    (the Log's :class:`FrameRecord` fingerprint), so a re-encoded or wrong clip can
    never pass off a different frame. Returns :class:`MissingEvidence` with reason
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
    "EvidenceStore",
    "EvidenceIndex",
    "EvidenceFrame",
    "MissingEvidence",
    "EvidenceSettings",
    "frame_from_clip",
    "DEFAULT_THUMB_WIDTH",
    "DEFAULT_MAX_FILES",
]
