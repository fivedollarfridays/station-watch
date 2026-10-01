"""Physics measurement scripts: capture-condition sweeps behind ``station-watch measure``.

Each script shares one runner (:mod:`station_watch.physics.runner`) and consumes the
HF2.8 manifest, selecting its clips by their optional per-clip ``tags``. The honesty
contract is stricter than ``evaluate``'s: a physics table that does not exist yet must
say so in the repo, so with no input a ``no_input`` file -- with no ``metrics`` key at
all -- is written and committed. The first script is :mod:`station_watch.physics.fps_sweep`.
"""
