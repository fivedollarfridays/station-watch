"""A looping :class:`SyntheticSource`: a never-ending normal-work frame stream.

A recorded clip ends; an hours-long soak needs a source that does not. These tests
prove that ``script=[...]`` with ``loop=True`` repeats its specs forever (so the
stream never runs dry), that the existing single-``spec`` behaviour is untouched when
no script is given, and that a non-looping script clamps at its last spec rather than
crashing. Specs are distinguished by ``dim`` (a frame rendered at a quarter of its
brightness), which survives the per-frame sensor noise as a clear mean-luma gap.
"""

from __future__ import annotations

from station_watch.capture.metrics import mean_luma
from station_watch.synth.source import SyntheticSource

_NOWAIT = {"monotonic": lambda: 0.0, "sleep": lambda _s: None}
_BRIGHT = {"positions": {"rail_pos_1": "present"}}
_DIM = {"positions": {"rail_pos_1": "present"}, "dim": True}


def _luma_sequence(source: SyntheticSource, n: int) -> list[float]:
    return [mean_luma(source.read()) for _ in range(n)]


def test_looping_script_repeats_its_specs_forever():
    source = SyntheticSource(script=[_BRIGHT, _DIM], loop=True, **_NOWAIT)
    luma = _luma_sequence(source, 6)
    bright = [luma[i] for i in (0, 2, 4)]
    dim = [luma[i] for i in (1, 3, 5)]
    assert min(bright) > max(dim), f"bright {bright} should all exceed dim {dim}"


def test_single_spec_behaviour_unchanged_without_a_script():
    # No script: every read renders the one spec, exactly as before.
    source = SyntheticSource(spec=_DIM, **_NOWAIT)
    luma = _luma_sequence(source, 3)
    plain = SyntheticSource(spec=_BRIGHT, **_NOWAIT)
    assert max(luma) < min(_luma_sequence(plain, 3))


def test_non_looping_script_clamps_at_its_last_spec():
    source = SyntheticSource(script=[_BRIGHT, _DIM], loop=False, **_NOWAIT)
    luma = _luma_sequence(source, 4)
    # After the two scripted frames, the last (dim) spec holds; no crash, no drift up.
    assert luma[0] > luma[1]
    assert max(luma[1:]) < luma[0]


def test_an_empty_script_is_refused():
    import pytest

    with pytest.raises(ValueError, match="script"):
        SyntheticSource(script=[], loop=True, **_NOWAIT)
