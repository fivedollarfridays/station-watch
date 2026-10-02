"""The evidence store's dedupe memory is bounded over an arbitrarily long run.

Every cited frame id used to be remembered forever, a slow leak on a station that
runs for days. Only ids that can still matter are kept: a frame still on disk is
among the newest ``max_files`` submissions, plus whatever is still queued. A
re-citation of a retained frame still dedupes (no second file, no false miss).
"""

from __future__ import annotations

import subprocess
import sys
import time

import numpy as np

from station_watch.evidence import EvidenceStore
from station_watch.evidence_index import EvidenceFrame, EvidenceIndex

RUN = "run-bounded"


def _frame(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, size=(24, 32, 3), dtype=np.uint8)


def test_dedupe_memory_stays_bounded_across_many_cited_frames(tmp_path):
    store = EvidenceStore(
        tmp_path / "ev", RUN, max_files=5, queue_size=4, ring_size=8, encode=lambda _: b"x"
    )
    frame = _frame(1)
    for frame_id in range(2000):
        store.note_frame(frame, frame_id, "t", "fp", None, had_observation=True)
        if frame_id % 3 == 0:
            time.sleep(0)  # let the writer drain now and then
    assert store.dedupe_size <= store.dedupe_limit == 5 + 4
    store.close()


def test_a_retained_frame_cited_again_still_dedupes(tmp_path):
    store = EvidenceStore(tmp_path / "ev", RUN, max_files=5, queue_size=4, ring_size=8)
    frames = {i: _frame(i) for i in range(4)}
    for frame_id, frame in frames.items():
        store.note_frame(frame, frame_id, "t", "fp", None, had_observation=True)
    store.note_blind_open(3)  # still retained: must not be re-written or marked missing
    store.close()
    assert store.written == 4 and store.dropped == 0
    assert isinstance(EvidenceIndex.load(tmp_path / "ev", RUN).lookup(3), EvidenceFrame)


def test_the_evidence_module_imports_cold_without_a_cycle():
    # evidence -> capture.metrics -> capture package -> capture.capture must not loop
    # back into a half-initialized station_watch.evidence.
    result = subprocess.run(
        [sys.executable, "-c", "import station_watch.evidence"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
