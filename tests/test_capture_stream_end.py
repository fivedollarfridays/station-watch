"""A file read to its end is a clean finish; a device handle can be reopened.

Capture tells the two "no frame" cases apart by the source: a recording that
reads nothing has ended (``stream_ended``, no blind record), while a live device
that reads nothing has failed. ``FrameSource.reopen`` is what Capture calls to
get a fresh handle on a replugged camera; on a file it simply starts over.
"""

import sys
from pathlib import Path

from station_watch.capture import Capture, FrameSource
from station_watch.log import Log

sys.path.insert(0, str(Path(__file__).parent))
from helpers.synth_video import write_synth_clip  # noqa: E402
from test_capture_blind import _blinds, _thresholds  # noqa: E402


def test_file_source_end_is_clean_and_writes_no_blind_record(tmp_path):
    path = write_synth_clip(tmp_path / "clip", frames=12, fps=30)
    log = Log(tmp_path / "end.db")
    capture = Capture(FrameSource(str(path)), station_id="S", camera_id="C", run_id="r")
    assert not capture.stream_ended
    capture.run(log, _thresholds(fiducial={**_thresholds().fiducial, "window_s": 100.0}))
    assert capture.stream_ended
    assert capture.first_frame.is_set()
    assert _blinds(log) == []


def test_reopen_gives_a_fresh_readable_handle(tmp_path):
    path = write_synth_clip(tmp_path / "clip", frames=3, fps=30)
    with FrameSource(str(path)) as source:
        while source.read() is not None:
            pass
        source.reopen()
        assert source.read() is not None
