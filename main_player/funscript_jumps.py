"""Getting to the scripted parts, within a video and across the playlist.

The companion to :mod:`main_player.clip_jumps`: those moves come from what Evolver
recorded about a clip, these from the funscript paired with it.  Both answer the
same shape of request -- take me somewhere better than here -- and both report
through the same notice channel, because Kino is the only one that can tell the
request had nowhere to go.
"""
from __future__ import annotations

import logging

from player_core.modes import NoticeLevel

logger = logging.getLogger(__name__)


class FunscriptJumps:
    def __init__(self, playback, notices) -> None:
        self._playback = playback
        self._notices = notices

    def jump_to_funscript(self) -> None:
        funscript = self._playback.current_funscript
        target = (
            None if funscript is None
            else funscript.next_active_ms(int(self._playback.position_ms))
        )
        if target is None:
            self._notices.say("no funscripting ahead", level=NoticeLevel.WARNING)
            return
        self._playback.seek_to(target)
        self._notices.say("funscript jump", level=NoticeLevel.HIGHLIGHT)

    def next_funscripted(self) -> None:
        entry = self._next_funscripted_entry()
        if entry is None:
            self._notices.say("no other funscripted video", level=NoticeLevel.WARNING)
            return
        index, video = entry
        self._playback.load(index)
        funscript = self._playback.current_funscript
        onset = None if funscript is None else funscript.first_real_event_ms
        if onset is not None:
            self._playback.seek_to(onset)
        logger.info("Next funscripted: %s", video.name)
        self._notices.say("next funscripted", level=NoticeLevel.HIGHLIGHT)

    def _next_funscripted_entry(self):
        playlist = self._playback.playlist
        current = self._playback.index
        for offset in range(1, len(playlist) + 1):
            index = (current + offset) % len(playlist)
            video = playlist[index]
            if self._playback.funscript_of(video) is not None and index != current:
                return index, video
        return None
