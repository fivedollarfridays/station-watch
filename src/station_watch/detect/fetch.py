"""``station-watch fetch-model``: download the keep-out weights, bounded and verified.

This is the only network call in the package, so it is fenced on every side:

* a socket **timeout** (:data:`FETCH_TIMEOUT_S`), so a stalled server fails instead
  of hanging the command forever;
* a **size cap** (:data:`MAX_MODEL_BYTES`), checked against a declared
  ``Content-Length`` before reading and again while streaming, so a misdirected or
  hostile response can never fill the disk;
* the pinned **SHA-256** (:func:`~station_watch.detect.yolox.verify_weights`).

The body streams to a ``.part`` file beside ``dest`` and is only renamed into place
once it is complete and hash-verified; any failure removes the partial file and
raises :class:`~station_watch.detect.yolox.WeightsError` with a message that names
the URL and what went wrong.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from station_watch.detect import yolox
from station_watch.detect.yolox import WeightsError, default_model_path, verify_weights

FETCH_TIMEOUT_S = 60.0
MAX_MODEL_BYTES = 64 * 1024 * 1024  # YOLOX-Nano is ~3.6 MB; generous, still bounded
_CHUNK = 1 << 16


def _too_large(size: int, max_bytes: int) -> WeightsError:
    return WeightsError(
        f"fetch-model: download from {yolox.MODEL_SOURCE_URL} exceeds the "
        f"{max_bytes}-byte cap ({size} bytes or more); refusing to write it"
    )


def _stream_to(response, part: Path, max_bytes: int) -> None:
    """Copy ``response`` into ``part``, refusing once it passes ``max_bytes``."""
    declared = getattr(response, "headers", {}).get("Content-Length")
    if declared is not None and int(declared) > max_bytes:
        raise _too_large(int(declared), max_bytes)
    written = 0
    with open(part, "wb") as handle:
        for chunk in iter(lambda: response.read(_CHUNK), b""):
            written += len(chunk)
            if written > max_bytes:
                raise _too_large(written, max_bytes)
            handle.write(chunk)


def fetch_model(
    dest: str | Path | None = None,
    *,
    opener=urllib.request.urlopen,
    timeout: float = FETCH_TIMEOUT_S,
    max_bytes: int = MAX_MODEL_BYTES,
) -> Path:
    """Download the official YOLOX weights to ``dest``, verify the hash, return the path.

    ``opener`` is the transport (``urllib.request.urlopen``; tests pass a fake).
    Raises :class:`WeightsError` on a timeout, a network error, an oversized body or
    a hash mismatch -- never leaving a partial or unverified file at ``dest``.
    """
    dest = Path(dest) if dest is not None else default_model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    url = yolox.MODEL_SOURCE_URL
    try:
        with opener(url, timeout=timeout) as response:
            _stream_to(response, part, max_bytes)
        verify_weights(part)
    except TimeoutError as exc:
        part.unlink(missing_ok=True)
        raise WeightsError(f"fetch-model: download from {url} timed out after {timeout} s") from exc
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise WeightsError(f"fetch-model: download from {url} failed: {exc}") from exc
    except WeightsError:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    return dest


__all__ = ["fetch_model", "FETCH_TIMEOUT_S", "MAX_MODEL_BYTES"]
