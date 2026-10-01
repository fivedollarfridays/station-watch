"""Child process: the real ``station-watch`` CLI with an endless synthetic camera.

There is no camera on a CI box, so this entry point swaps the one piece that talks
to hardware -- ``FrameSource`` for a device index -- for a live-paced
:class:`~station_watch.synth.source.SyntheticSource` that never runs out of frames,
then hands ``argv`` to the real ``station_watch.cli.main``. Everything else
(argument parsing, startup checks, Runner, Capture thread, Log, Judge, Alarm, the
drill's stdin marks and shutdown) is production -- so a live drill driven through
this child ends only when stdin does, exactly as it would on a real camera.

Usage: ``python _synthetic_camera_child.py drill --config ... --live --source 0 ...``
"""

import sys

import station_watch.runner.startup as startup
from station_watch.cli import main
from station_watch.synth.source import SyntheticSource


def _install() -> None:
    real = startup.FrameSource

    def fake_source(spec):
        if isinstance(spec, int):
            return SyntheticSource(fps=20.0)
        return real(spec)

    startup.FrameSource = fake_source


if __name__ == "__main__":
    _install()
    sys.exit(main(sys.argv[1:]))
