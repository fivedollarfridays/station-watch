"""The evaluation manifest: a committed, text description of a labeled clip set.

A manifest is YAML. Clips are grouped *by recording session* so a train / test
split is always by session, never by adjacent frames (which would leak). Each
clip names a path relative to a clips directory (clips themselves stay local and
gitignored under ``data/local/clips/``; only these labels are committed, under
``measurements/labels/``) and the labeled intervals a run is scored against:

```yaml
dataset: line-3-week-of-2026-09-28
sessions:
  - id: session-a
    clips:
      - path: session-a/clip-01.mkv
        positions:                       # rail-position state over a frame span
          - {target: rail_pos_1, state: present, start_frame: 0, end_frame: 120}
          - {target: rail_pos_2, state: absent,  start_frame: 0, end_frame: 120}
        stalls:                          # station-zone stall intervals
          - {start_frame: 40, end_frame: 110}
        keepouts:                        # a person in a keep-out zone
          - {zone: zone_press, start_frame: 20, end_frame: 35}
        occlusions:                      # a rail position covered (e.g. by a hand)
          - {target: rail_pos_1, start_frame: 30, end_frame: 60}
        camera_faults:                   # injected sensor faults, with a start frame
          - {reason: dark, start_frame: 60}
        creeps:                          # a cycle-time-creep interval (K12)
          - {zone: bench}
```

``state`` is one of ``present`` / ``absent`` / ``unknown`` -- the three states the
rail-position detector can confirm (``not_seated`` reads as ``absent``).

**Honesty rules (the harness never reports numbers over a partial set):**

* An *absent* ``--manifest`` (none given) or one whose file does not exist raises
  :class:`NoLabeledSetError`; the CLI prints ``no labeled set present: <path>``
  and exits non-zero without writing a file.
* A manifest that lists a clip whose file is missing raises
  :class:`ManifestError` naming that clip, and again nothing is written.
* A clip path that is absolute or whose ``..`` climbs out of the clips directory
  raises :class:`ManifestError`: a manifest never reads a file outside it.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class NoLabeledSetError(RuntimeError):
    """No labeled set is present: the manifest was not given or does not exist."""

    def __init__(self, path: str | None) -> None:
        self.path = path if path else "(no --manifest given)"
        super().__init__(f"no labeled set present: {self.path}")


class ManifestError(RuntimeError):
    """The manifest is present but invalid (e.g. it names a clip that is missing)."""


@dataclass(frozen=True)
class ClipLabel:
    """Ground truth for one clip: its session, resolved path, and labeled intervals.

    ``tags`` is an optional per-clip mapping of capture conditions (for example
    ``fps``, ``exposure_s``, ``speed_m_s``, ``lamp``, ``angle_deg``). It is how a
    physics measurement selects the clips it consumes without touching the HF2
    scoring intervals; an HF2 manifest with no ``tags`` loads exactly as before.

    ``split`` (``calibration`` / ``held_out``), ``recorded_on`` (``YYYY-MM-DD``) and
    ``clip_sha256`` carry the HF3.8 train/test separation: a session's ``split`` and
    ``recorded_on`` propagate to each of its clips (absent ``split`` means
    ``calibration``, so every HF2 manifest loads unchanged), and ``clip_sha256`` is
    the SHA-256 of the clip's bytes, computed at load -- the identity the held-out
    calibration check traces against.
    """

    session: str
    rel_path: str
    clip_path: Path
    positions: list[dict] = field(default_factory=list)
    stalls: list[dict] = field(default_factory=list)
    keepouts: list[dict] = field(default_factory=list)
    camera_faults: list[dict] = field(default_factory=list)
    creeps: list[dict] = field(default_factory=list)
    occlusions: list[dict] = field(default_factory=list)
    tags: dict = field(default_factory=dict)
    split: str = "calibration"
    recorded_on: str | None = None
    clip_sha256: str = ""


@dataclass(frozen=True)
class Manifest:
    """A parsed manifest: its dataset name, its clips, and its content hash."""

    dataset: str
    sha256: str
    clips: list[ClipLabel]

    @property
    def sessions(self) -> list[str]:
        """Distinct session ids, in first-seen order."""
        seen: list[str] = []
        for clip in self.clips:
            if clip.session not in seen:
                seen.append(clip.session)
        return seen


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _contained_clip_path(rel: str, clips_dir: Path, session: str) -> Path:
    """``rel`` under ``clips_dir``, refusing absolute paths and ``..`` that escape it.

    The manifest is untrusted text; a clip path must never reach a file outside the
    clips directory. Containment is checked on the normalized path (lexically, so an
    operator's symlinked clip store still works).
    """
    base = os.path.abspath(clips_dir)
    candidate = os.path.abspath(os.path.join(base, str(rel)))
    if os.path.isabs(str(rel)) or os.path.commonpath([base, candidate]) != base:
        raise ManifestError(
            f"manifest clip path escapes the clips directory: {rel!r} "
            f"(clips dir {clips_dir}, session {session})"
        )
    return Path(candidate)


SPLITS = ("calibration", "held_out")


def _clip_label(session: str, entry: dict, clips_dir: Path, *, split: str, recorded_on) -> ClipLabel:
    rel = entry["path"]
    clip_path = _contained_clip_path(rel, clips_dir, session)
    if not clip_path.exists():
        raise ManifestError(f"manifest clip not found: {clip_path} (session {session})")
    return ClipLabel(
        session=session,
        rel_path=rel,
        clip_path=clip_path,
        positions=list(entry.get("positions", [])),
        stalls=list(entry.get("stalls", [])),
        keepouts=list(entry.get("keepouts", [])),
        camera_faults=list(entry.get("camera_faults", [])),
        creeps=list(entry.get("creeps", [])),
        occlusions=list(entry.get("occlusions", [])),
        tags=dict(entry.get("tags", {})),
        split=split,
        recorded_on=recorded_on,
        clip_sha256=sha256_file(clip_path),
    )


def _session_split(session: dict) -> str:
    split = session.get("split", "calibration")
    if split not in SPLITS:
        raise ManifestError(
            f"manifest session {session.get('id')!r} has unknown split {split!r} "
            f"(expected one of {', '.join(SPLITS)})"
        )
    return split


def load_manifest(manifest_path: str | None, clips_dir: str | Path) -> Manifest:
    """Load and validate a manifest, resolving and checking every clip path.

    Raises :class:`NoLabeledSetError` when ``manifest_path`` is absent or missing,
    and :class:`ManifestError` when the manifest names a clip that does not exist.
    """
    if manifest_path is None or not Path(manifest_path).exists():
        raise NoLabeledSetError(manifest_path)
    data = yaml.safe_load(Path(manifest_path).read_text())
    if not isinstance(data, dict):
        raise ManifestError(f"manifest must be a YAML mapping, got {type(data).__name__}")
    clips_dir = Path(clips_dir)
    clips: list[ClipLabel] = []
    for session in data.get("sessions", []):
        session_id = session["id"]
        split = _session_split(session)
        recorded_on = session.get("recorded_on")
        for entry in session.get("clips", []):
            clips.append(
                _clip_label(session_id, entry, clips_dir, split=split, recorded_on=recorded_on)
            )
    if not clips:
        raise ManifestError(f"manifest lists no clips: {manifest_path}")
    return Manifest(
        dataset=data.get("dataset", "unnamed"),
        sha256=sha256_file(manifest_path),
        clips=clips,
    )


__all__ = [
    "NoLabeledSetError",
    "ManifestError",
    "ClipLabel",
    "Manifest",
    "sha256_file",
    "load_manifest",
]
