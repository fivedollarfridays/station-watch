"""``station-watch fetch-model``: bounded in time and size, hash-verified, fail loud.

The download is the package's only network call, so it must never hang forever on
a stalled server or fill the disk from a hostile/misdirected response. Every test
here swaps the transport for an in-memory fake -- no real network.
"""

from __future__ import annotations

import hashlib
import io

import pytest

from station_watch.detect import yolox
from station_watch.detect.fetch import fetch_model
from station_watch.detect.yolox import WeightsError


class _Response(io.BytesIO):
    def __init__(self, body: bytes, content_length: int | None = None) -> None:
        super().__init__(body)
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}


def _opener(body: bytes, *, content_length=None, calls=None):
    def open_url(url, timeout=None):
        if calls is not None:
            calls.append({"url": url, "timeout": timeout})
        return _Response(body, content_length)

    return open_url


@pytest.fixture
def known_body(monkeypatch):
    body = b"pretend onnx weights"
    monkeypatch.setattr(yolox, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    return body


def test_fetch_passes_a_timeout_and_writes_verified_weights(tmp_path, known_body):
    calls = []
    dest = tmp_path / "models" / "yolox_nano.onnx"

    path = fetch_model(dest, opener=_opener(known_body, calls=calls))

    assert path == dest
    assert dest.read_bytes() == known_body
    assert calls[0]["url"] == yolox.MODEL_SOURCE_URL
    assert calls[0]["timeout"] is not None and calls[0]["timeout"] > 0


def test_body_over_the_size_cap_fails_loud_and_leaves_no_file(tmp_path, known_body):
    dest = tmp_path / "yolox_nano.onnx"

    with pytest.raises(WeightsError, match="exceeds the .* cap"):
        fetch_model(dest, opener=_opener(b"x" * 101), max_bytes=100)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []  # no partial file left behind


def test_declared_content_length_over_the_cap_is_refused_before_reading(tmp_path, known_body):
    dest = tmp_path / "yolox_nano.onnx"

    with pytest.raises(WeightsError, match="exceeds the .* cap"):
        fetch_model(dest, opener=_opener(b"", content_length=10_000), max_bytes=100)

    assert not dest.exists()


def test_hash_mismatch_fails_loud_and_leaves_no_file(tmp_path, known_body):
    dest = tmp_path / "yolox_nano.onnx"

    with pytest.raises(WeightsError, match="hash mismatch"):
        fetch_model(dest, opener=_opener(b"tampered weights"))

    assert list(tmp_path.iterdir()) == []


def test_timeout_fails_loud_naming_the_url(tmp_path, known_body):
    def stalled(url, timeout=None):
        raise TimeoutError("timed out")

    with pytest.raises(WeightsError, match="timed out") as exc:
        fetch_model(tmp_path / "yolox_nano.onnx", opener=stalled)
    assert yolox.MODEL_SOURCE_URL in str(exc.value)
