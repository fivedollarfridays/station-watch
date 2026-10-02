"""Argument wiring for ``station-watch fetch-model``, kept beside the fetcher.

Like the other lazy subcommands, building the parser here imports nothing heavy;
the download and hash check (:mod:`station_watch.detect.fetch`) load in
:func:`handle`. This is the only command in the package that touches the network.
"""

from __future__ import annotations

import sys


def add_parser(sub) -> None:
    """Add the ``fetch-model`` subparser and its arguments to ``sub``."""
    fetch = sub.add_parser(
        "fetch-model",
        help="download the keep-out person model (Apache-2.0 YOLOX) and verify its hash",
        description="Download the YOLOX-Nano ONNX weights (Apache-2.0) from the official "
        "release into data/local/models/ and verify the SHA-256. This is the only network "
        "call in the package; `run` never makes it. Weights are never committed.",
    )
    fetch.add_argument(
        "--dest",
        help="where to write the weights (default: data/local/models/yolox_nano.onnx)",
    )


def handle(args) -> int:
    """Fetch and verify the weights; 0 on success, 1 naming the failure."""
    from station_watch.detect.fetch import fetch_model
    from station_watch.detect.yolox import (
        MODEL_LICENSE,
        MODEL_NAME,
        MODEL_SOURCE_URL,
        WeightsError,
    )

    print(f"station-watch: fetching {MODEL_NAME} ({MODEL_LICENSE}) from {MODEL_SOURCE_URL}")
    try:
        path = fetch_model(args.dest)
    except (WeightsError, OSError) as exc:
        print(f"station-watch: fetch-model failed: {exc}", file=sys.stderr)
        return 1
    print(f"station-watch: verified weights written to {path}")
    return 0


__all__ = ["add_parser", "handle"]
