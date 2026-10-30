from __future__ import annotations

import logging
from pathlib import Path

from funestra.modes import NoticeLevel, read_mode

from .bridge_records import BridgeConfig
from .event_log import (
    FAVORITE,
    NOTICE,
    SOURCE_LANDSCAPE,
    SOURCE_MAIN,
    SOURCE_PORTRAIT,
    notice,
)
from .room_funestras import RoomFunestra

logger = logging.getLogger(__name__)

_LEVELS = {NoticeLevel.WARNING: logging.WARNING, NoticeLevel.HIGHLIGHT: FAVORITE}


def read_funestra_notice(path) -> tuple[float, NoticeLevel, str]:
    """A Funestra's latest one-shot notice as (sequence, level, message); the
    Funestra bumps the sequence whenever it raises one, and (0, "", "") means
    there is nothing to read."""
    try:
        values = dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        return (float(values.get("seq", "0")),
                read_mode(NoticeLevel, values.get("level", ""), NoticeLevel.NOTICE),
                values.get("message", ""))
    except (OSError, ValueError):
        return 0, "", ""


def notice_channels(config: BridgeConfig) -> dict[str, Path]:
    """Where each Funestra publishes its one-shot notices, by the source its
    notices flash over."""
    channels = ((SOURCE_MAIN, config.main_funestra_notice_file),
                *((source, config.satellite(funestra).notice_file)
                  for source, funestra in ((SOURCE_PORTRAIT, RoomFunestra.PORTRAIT),
                                           (SOURCE_LANDSCAPE, RoomFunestra.LANDSCAPE))))
    return {source: path for source, path in channels if path is not None}


class FunestraNotices:
    """What each Funestra has raised since the last tick, flashed once over that
    Funestra.  A Funestra names the kind rather than the color: a warning for a
    request with nowhere to go, a highlight for one about a funscript -- which is
    what green is kept for here -- and anything else an ordinary white notice.
    Whatever is on disk when the session opens is latched, never flashed."""

    def __init__(self, config: BridgeConfig) -> None:
        self._channels = notice_channels(config)
        self._last_seq = {source: read_funestra_notice(path)[0]
                          for source, path in self._channels.items()}

    def flash_new(self) -> None:
        for source, path in self._channels.items():
            seq, level, message = read_funestra_notice(path)
            if seq <= self._last_seq.get(source, 0):
                continue
            self._last_seq[source] = seq
            if message:
                notice(logger, message, source=source, level=_LEVELS.get(level, NOTICE))
