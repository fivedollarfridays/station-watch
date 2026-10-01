"""The keep-out person detector: YOLOX-Nano, Apache-2.0, run locally via ``cv2.dnn``.

Model:   YOLOX-Nano (object detection; COCO person is class 0)
License: Apache-2.0 -- https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE
Source:  https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx
SHA-256: c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d

The weights are **never committed** (``data/local/models/`` is gitignored). Fetch
them once with ``station-watch fetch-model`` (:mod:`station_watch.detect.fetch`): it
downloads the file from the official YOLOX release into ``data/local/models/`` --
bounded by a timeout and a size cap -- and verifies the SHA-256 above.
That download is the *only* network call in the whole package, and ``run`` never
makes it -- a run with keep-out zones configured requires the weights to already
be present and to match the hash, or startup refuses (K9).

The model runs through OpenCV's ``cv2.dnn`` (already a dependency), so keep-out
adds no new Python package. ``YoloxBackend.detect_people`` is the keep-out backend
protocol: ``detect_people(frame) -> list[(x0, y0, x1, y1, score)]`` for every
person box, in the frame's own pixel coordinates.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

MODEL_NAME = "YOLOX-Nano"
MODEL_LICENSE = "Apache-2.0"
MODEL_FILENAME = "yolox_nano.onnx"
MODEL_SOURCE_URL = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
)
MODEL_SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"

_MODELS_DIR = Path("data/local/models")
_INPUT_SIZE = (416, 416)  # (height, width)
_STRIDES = (8, 16, 32)
_PERSON_CLASS = 0
_PAD_VALUE = 114


class WeightsError(RuntimeError):
    """The model weights are missing or do not match the expected SHA-256."""


def default_model_path() -> Path:
    """The on-disk path ``fetch-model`` writes to and a run reads from."""
    return _MODELS_DIR / MODEL_FILENAME


def sha256_of(path: str | Path) -> str:
    """The SHA-256 hex digest of ``path`` (read in chunks; the file may be large)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_weights(path: str | Path) -> Path:
    """Return ``path`` if it exists and hashes to :data:`MODEL_SHA256`, else raise.

    The message always names the file so a misconfigured station fails loud (K9).
    """
    path = Path(path)
    if not path.exists():
        raise WeightsError(
            f"keep-out model weights not found: {path} -- run `station-watch fetch-model`"
        )
    actual = sha256_of(path)
    if actual != MODEL_SHA256:
        raise WeightsError(
            f"keep-out model weights hash mismatch: {path} "
            f"(expected {MODEL_SHA256}, got {actual}) -- re-run `station-watch fetch-model`"
        )
    return path


def _preprocess(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """Letterbox ``frame`` into the model's square input; return (blob, scale ratio)."""
    height, width = frame.shape[:2]
    ratio = min(_INPUT_SIZE[0] / height, _INPUT_SIZE[1] / width)
    resized = cv2.resize(frame, (int(width * ratio), int(height * ratio)))
    padded = np.full((_INPUT_SIZE[0], _INPUT_SIZE[1], 3), _PAD_VALUE, dtype=np.uint8)
    padded[: resized.shape[0], : resized.shape[1]] = resized
    blob = cv2.dnn.blobFromImage(padded, 1.0, (_INPUT_SIZE[1], _INPUT_SIZE[0]), swapRB=False)
    return blob, ratio


def _grids_and_strides() -> tuple[np.ndarray, np.ndarray]:
    """The (x, y) grid offsets and matching stride for every YOLOX anchor cell."""
    grids, strides = [], []
    for stride in _STRIDES:
        hsize, wsize = _INPUT_SIZE[0] // stride, _INPUT_SIZE[1] // stride
        xv, yv = np.meshgrid(np.arange(wsize), np.arange(hsize))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((hsize * wsize, 1), stride))
    return np.concatenate(grids, 0), np.concatenate(strides, 0)


def _decode(output: np.ndarray) -> np.ndarray:
    """Turn raw YOLOX outputs into absolute [cx, cy, w, h, obj, class...] rows."""
    grids, strides = _grids_and_strides()
    output = output.copy()
    output[:, :2] = (output[:, :2] + grids) * strides
    output[:, 2:4] = np.exp(output[:, 2:4]) * strides
    return output


def _person_boxes(decoded: np.ndarray, ratio: float, score_threshold: float) -> list[tuple]:
    """Person boxes above ``score_threshold``, scaled back to frame pixels, as xyxy."""
    scores = decoded[:, 4] * decoded[:, 5 + _PERSON_CLASS]
    keep = scores > score_threshold
    boxes, scores = decoded[keep, :4], scores[keep]
    xyxy = np.empty_like(boxes)
    xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    xyxy /= ratio
    return _nms(xyxy, scores)


def _nms(xyxy: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.45) -> list[tuple]:
    """Non-max-suppress the boxes and return ``(x0, y0, x1, y1, score)`` tuples."""
    if len(xyxy) == 0:
        return []
    wh = np.column_stack((xyxy[:, 0], xyxy[:, 1], xyxy[:, 2] - xyxy[:, 0], xyxy[:, 3] - xyxy[:, 1]))
    idxs = cv2.dnn.NMSBoxes(wh.tolist(), scores.tolist(), 0.0, iou_threshold)
    out = []
    for i in np.array(idxs).reshape(-1):
        x0, y0, x1, y1 = xyxy[i]
        out.append((float(x0), float(y0), float(x1), float(y1), float(scores[i])))
    return out


class YoloxBackend:
    """The keep-out backend: YOLOX-Nano person detection through ``cv2.dnn``."""

    def __init__(self, model_path: str | Path, *, score_threshold: float = 0.3) -> None:
        self._net = cv2.dnn.readNetFromONNX(str(model_path))
        self._score_threshold = score_threshold

    def detect_people(self, frame: np.ndarray) -> list[tuple]:
        """Every person box in ``frame`` as ``(x0, y0, x1, y1, score)`` in frame pixels."""
        blob, ratio = _preprocess(frame)
        self._net.setInput(blob)
        decoded = _decode(self._net.forward()[0])
        return _person_boxes(decoded, ratio, self._score_threshold)


__all__ = [
    "YoloxBackend",
    "WeightsError",
    "verify_weights",
    "sha256_of",
    "default_model_path",
    "MODEL_NAME",
    "MODEL_LICENSE",
    "MODEL_SOURCE_URL",
    "MODEL_SHA256",
    "MODEL_FILENAME",
]
