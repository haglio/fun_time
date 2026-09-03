"""The favs.csv HYPERLINK format, in one place.

favs.csv is public surface — evolver reads it, and it opens by hand in a
spreadsheet — and its cells are Excel HYPERLINK formulas.  The writer
(media_actions) and the reader (random_favs_browser) share these, held
together by a round-trip test."""
from __future__ import annotations

FAVS_HEADER = "local_file,web_url"

_HYPERLINK_PREFIX = '=HYPERLINK("'
_HYPERLINK_SEP = '";"'
_HYPERLINK_SUFFIX = '")'


def hyperlink_cell(target: str, display: str) -> str:
    """The Excel formula linking *target* under *display*'s text."""
    return f"{_HYPERLINK_PREFIX}{target}{_HYPERLINK_SEP}{display}{_HYPERLINK_SUFFIX}"


def hyperlink_parts(cell: str) -> tuple[str, str] | None:
    """Split a :func:`hyperlink_cell` formula back into (target, display)."""
    if not cell.startswith(_HYPERLINK_PREFIX) or not cell.endswith(_HYPERLINK_SUFFIX):
        return None
    inner = cell[len(_HYPERLINK_PREFIX) : -len(_HYPERLINK_SUFFIX)]
    separator = inner.find(_HYPERLINK_SEP)
    if separator == -1:
        return None
    return inner[:separator], inner[separator + len(_HYPERLINK_SEP) :]
