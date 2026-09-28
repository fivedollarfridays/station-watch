"""Child process: the real ``station-watch`` CLI with a fake *live* camera plugged in.

There is no camera on a CI box, so this entry point swaps the one piece that
talks to hardware -- ``FrameSource`` for a device index -- for a
:class:`~helpers.live_sources.FailingLiveSource`, then hands ``argv`` to the real
``station_watch.cli.main``. Everything else (argument parsing, startup checks,
Runner, Capture thread, Log, Judge, Alarm, sinks, signal handling) is production.

Usage: ``python _fake_camera_child.py <frames_before> <mode> <recover_after_s|none>
run --config ... --source 0 --log ...``
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import station_watch.runner.startup as startup  # noqa: E402
from helpers.live_sources import FailingLiveSource  # noqa: E402
from station_watch.cli import main  # noqa: E402


def _install(frames_before: int, mode: str, recover: float | None) -> None:
    real = startup.FrameSource

    def fake_source(spec):
        if isinstance(spec, int):
            return FailingLiveSource(frames_before, mode=mode, recover_after_s=recover)
        return real(spec)

    startup.FrameSource = fake_source


if __name__ == "__main__":
    frames, mode, recover, *argv = sys.argv[1:]
    _install(int(frames), mode, None if recover == "none" else float(recover))
    sys.exit(main(argv))
