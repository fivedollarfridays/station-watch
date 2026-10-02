"""Parse the Board's HTML page the way a browser would -- with ``html.parser``.

The Board QA tests assert against the *rendered page*, not just the view model,
so a renderer that drops the staleness into the status would be caught. These
helpers pull the two status-bearing texts back out of the page: the ``<h1>``
station/state heading and the ``<b>`` inside the ``status:`` paragraph (``OK`` or
``NOT OK``).
"""

from __future__ import annotations

from html.parser import HTMLParser


class _StatusParser(HTMLParser):
    """Collects the text of the first ``<h1>`` and the first ``<b>`` element."""

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[str] = []
        self.h1: list[str] = []
        self.b: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._stack:
            # Pop back to and including the matching tag.
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if "h1" in self._stack:
            self.h1.append(data)
        if "b" in self._stack:
            self.b.append(data)


def parse_status(html: str) -> tuple[str, str]:
    """Return ``(status_text, h1_text)`` parsed from the Board page.

    ``status_text`` is ``OK`` or ``NOT OK`` (the status paragraph's ``<b>``);
    ``h1_text`` is the station/state heading, e.g. ``Station station-1 — FAULT``.
    """
    parser = _StatusParser()
    parser.feed(html)
    return "".join(parser.b).strip(), "".join(parser.h1).strip()


__all__ = ["parse_status"]
