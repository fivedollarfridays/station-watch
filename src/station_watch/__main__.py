"""Entry point for ``python -m station_watch`` (mirrors the console script)."""

from __future__ import annotations

import sys

from station_watch.cli import main

if __name__ == "__main__":
    sys.exit(main())
