from __future__ import annotations

import logging
from pathlib import Path

from funestra.messages import MessageReader
from funestra.modes import MessageLevel

from .bridge_records import BridgeConfig
from .command_value import FUNESTRA_SOURCES
from .event_log import (
    FAVORITE,
    MESSAGE,
    flash_message,
)

logger = logging.getLogger(__name__)

_LEVELS = {MessageLevel.WARNING: logging.WARNING, MessageLevel.HIGHLIGHT: FAVORITE,
           MessageLevel.ERROR: logging.ERROR}


def notice_channels(config: BridgeConfig) -> dict[Path, str]:
    return {path: FUNESTRA_SOURCES[funestra]
            for funestra, channel in config.funestras.items() for path in channel.notice_files}


class FunestraMessages:
    def __init__(self, config: BridgeConfig) -> None:
        self._readers = [(MessageReader(path), source)
                         for path, source in notice_channels(config).items()]

    def flash_new(self) -> None:
        for reader, source in self._readers:
            said = reader.take_new()
            if said is not None:
                flash_message(logger, said.message, source=source,
                              level=_LEVELS.get(said.level, MESSAGE), mark=said.mark)
