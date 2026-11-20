from __future__ import annotations

import logging
from pathlib import Path

from funestra.messages import MessageReader, SaidMessage, read_message
from funestra.modes import MessageLevel

from .bridge_records import BridgeConfig
from .command_value import FUNESTRA_SOURCES
from .event_log import (
    FAVORITE,
    MESSAGE,
    flash_message,
)
from .polled_files import file_stamp

logger = logging.getLogger(__name__)

_LEVELS = {MessageLevel.WARNING: logging.WARNING, MessageLevel.HIGHLIGHT: FAVORITE,
           MessageLevel.ERROR: logging.ERROR}


def notice_channels(config: BridgeConfig) -> dict[Path, str]:
    return {path: FUNESTRA_SOURCES[funestra]
            for funestra, channel in config.funestras.items() for path in channel.notice_files}


_NOT_LOOKED_AT = object()


class _NoticeFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._reader = MessageReader(path)
        self._read_as_of: object = _NOT_LOOKED_AT

    def take_new(self) -> SaidMessage | None:
        stamp = file_stamp(self._path)
        if stamp == self._read_as_of:
            return None
        if stamp is not None and not read_message(self._path).seq:
            return None
        self._read_as_of = stamp
        return self._reader.take_new()


class FunestraMessages:
    def __init__(self, config: BridgeConfig) -> None:
        self._notices = [(_NoticeFile(path), source)
                         for path, source in notice_channels(config).items()]

    def flash_new(self) -> None:
        for notice, source in self._notices:
            said = notice.take_new()
            if said is not None:
                flash_message(logger, said.message, source=source,
                              level=_LEVELS.get(said.level, MESSAGE), mark=said.mark)
