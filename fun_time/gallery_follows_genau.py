from __future__ import annotations

from pathlib import Path

from player_core.file_channel import append_command

from .player_status import read_genau_status


class GalleryFollowsGenau:
    def __init__(self, *, genau_status_file: Path, origenerator_cmd_file: Path | None) -> None:
        self._genau_status_file = genau_status_file
        self._origenerator_cmd_file = origenerator_cmd_file
        self._expecting_a_lock = False

    def expect_a_lock(self) -> None:
        self._expecting_a_lock = True

    def sync(self) -> None:
        if not self._expecting_a_lock:
            return
        genau = read_genau_status(self._genau_status_file)
        if genau.locked and genau.clip:
            self._expecting_a_lock = False
            append_command(self._origenerator_cmd_file, f"GO_TO|{genau.clip}")
