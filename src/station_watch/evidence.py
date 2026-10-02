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
than silently returning nothing (or, worse, a neighbouring frame).

Writes go through a bounded queue drained by a single writer thread: a full queue
drops the frame and counts it, a write that raises is counted and reported once,
and neither is ever raised back into Capture (K5, K10) -- a run with a broken
evidence writer records exactly the verdicts, alarms and cycles it would with
evidence off.

The read side (:class:`EvidenceIndex`, :class:`EvidenceFrame`,
:class:`MissingEvidence`, :func:`frame_from_clip`) that HF3.5 consumes lives in
:mod:`station_watch.evidence_index` and is re-exported here, so
``from station_watch.evidence import EvidenceIndex`` keeps working; the pure
thumbnail helpers live in :mod:`station_watch.evidence_image`.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from station_watch.capture.metrics import fingerprint as _fingerprint
from station_watch.evidence_image import downscale, encode_jpeg, scaled_corners
from station_watch.evidence_index import (
    DROPPED,
    INDEX_NAME,
    MISSES_NAME,
    PRUNED,
    WRITE_FAILED,
    EvidenceFrame,
    EvidenceIndex,
    MissingEvidence,
    frame_from_clip,
)

# Documented defaults for the optional ``evidence:`` config section.
DEFAULT_THUMB_WIDTH = 320
DEFAULT_MAX_FILES = 2000
# Capture-side buffering. The queue bounds how many pending writes can pile up
# before frames are dropped; the ring retains recent frames so the last good one
# before a blind opens is still available to write.
DEFAULT_QUEUE_SIZE = 256
DEFAULT_RING_SIZE = 64


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
        encode: Callable[[np.ndarray], bytes] = encode_jpeg,
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
        # A separate lock for the misses file: it is appended from both the capture
        # thread (a dropped frame) and the writer thread (a failed or pruned one),
        # and the capture thread already holds self._lock when it does, so reusing
        # it would deadlock. The writer thread never takes self._lock, so there is
        # no lock-ordering inversion.
        self._miss_lock = threading.Lock()
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
        self._stopped = False
        self._thread = threading.Thread(
            target=self._writer_loop, name="station-watch-evidence", daemon=True
        )
        self._thread.start()

    # --- Capture-thread facing API (must never raise) ---------------------------

    def note_frame(
        self, frame, frame_id: int, ts: str, fingerprint: str, corners, *, had_observation: bool
    ) -> None:
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
            # A last-good frame may never have produced an observation, so Capture
            # has no metadata for it; the fingerprint is the kept pixels' hash
            # (identical to the Log's FrameRecord) and ts is left empty.
            ts, fingerprint = "", _fingerprint(frame)
        job = _Job(frame_id, frame, ts, fingerprint, corners)
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self.dropped += 1
            self._record_miss(frame_id, DROPPED)
            return
        self._submitted.add(frame_id)

    # --- Writer thread ----------------------------------------------------------

    def _writer_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            self._write_one(job)

    def _write_one(self, job: _Job) -> None:
        try:
            thumbnail, scale = downscale(job.frame, self._thumb_width)
            data = self._encode(thumbnail)
            name = f"frame_{job.frame_id:08d}.jpg"
            (self._dir / name).write_bytes(data)
            height, width = thumbnail.shape[:2]
            self._append_index(job, name, width, height, scale)
            self.written += 1
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
            "corners": scaled_corners(job.corners, scale),
        }
        self._index.write(json.dumps(entry) + "\n")
        self._index.flush()
        self._written[job.frame_id] = name
        while len(self._written) > self._max_files:
            frame_id, old = self._written.popitem(last=False)
            (self._dir / old).unlink(missing_ok=True)
            self.pruned += 1
            self._record_miss(frame_id, PRUNED)

    def _record_miss(self, frame_id: int, reason: str) -> None:
        """Persist why a frame id has no readable evidence (last writer wins)."""
        line = json.dumps({"frame_id": frame_id, "reason": reason}) + "\n"
        with self._miss_lock:
            self._misses.write(line)
            self._misses.flush()

    def close(self) -> None:
        """Drain the queue, stop the writer thread, and close the index files."""
        if self._stopped:
            return
        self._stopped = True
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        self._index.close()
        self._misses.close()


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
