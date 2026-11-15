from __future__ import annotations

import logging
from pathlib import Path

from funestra.modes import MessageLevel, read_mode

from .bridge_records import BridgeConfig
from .command_value import FUNESTRA_SOURCES
from .event_log import (
    FAVORITE,
    MESSAGE,
    flash_message,
)

logger = logging.getLogger(__name__)

_LEVELS = {MessageLevel.WARNING: logging.WARNING, MessageLevel.HIGHLIGHT: FAVORITE}


def read_funestra_message(path) -> tuple[float, MessageLevel, str]:
    try:
        values = dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        return (float(values.get("seq", "0")),
                read_mode(MessageLevel, values.get("level", ""), MessageLevel.NORMAL),
                values.get("message", ""))
    except (OSError, ValueError):
        return 0, "", ""


def notice_channels(config: BridgeConfig) -> dict[str, Path]:
    return {FUNESTRA_SOURCES[funestra]: channel.notice_file
            for funestra, channel in config.funestras.items() if channel.notice_file is not None}


class FunestraMessages:
    def __init__(self, config: BridgeConfig) -> None:
        self._channels = notice_channels(config)
        self._last_seq = {source: read_funestra_message(path)[0]
                          for source, path in self._channels.items()}

    def flash_new(self) -> None:
        for source, path in self._channels.items():
            seq, level, message = read_funestra_message(path)
            if seq <= self._last_seq.get(source, 0):
                continue
            self._last_seq[source] = seq
            if message:
                flash_message(logger, message, source=source, level=_LEVELS.get(level, MESSAGE))
