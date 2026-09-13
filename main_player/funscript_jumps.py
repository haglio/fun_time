"""Getting to the scripted parts, within a video and across the playlist.

The companion to :mod:`main_player.clip_jumps`: those moves come from what Evolver
recorded about a clip, these from the funscript paired with it.  Both answer the
same shape of request — take me somewhere better than here — and both report
through the same notice channel, because the main player is the only one that can tell the
request had nowhere to go.

A scripted video is mostly not scripted: a funscript's action comes in runs with
quiet stretches between them (which is why video mode has the Robot Hand fill
those in at all).  "Jump to funscript" skips the stretch you are in; "next funscripted"
gives up on this video and finds one that is scripted, landing on its action
rather than at its top.
"""
from __future__ import annotations

import logging

from player_core.modes import NoticeLevel

logger = logging.getLogger(__name__)


class FunscriptJumps:
    """Fun Time's two funscript navigations, over one session's playlist."""

    def __init__(self, session, notices) -> None:
        self._session = session
        self._notices = notices

    def jump_to_funscript(self) -> None:
        """Skip ahead to where this video's scripting next starts up.

        An unscripted video and one whose last run is already past the
        playhead answer the same way: there is nothing ahead to jump to.  That
        is an ordinary outcome rather than an error, so it is said rather than
        logged and swallowed.
        """
        funscript = self._session.current_funscript
        target = (
            None if funscript is None
            else funscript.next_active_ms(int(self._session.position_ms))
        )
        if target is None:
            self._notices.say("no funscripting ahead", level=NoticeLevel.WARNING)
            return
        self._session.seek_to(target)
        self._notices.say("funscript jump", level=NoticeLevel.HIGHLIGHT)

    def next_funscripted(self) -> None:
        """Move to the next playlist entry that has a funscript, at its action.

        Forward from where we are and wrapping, so it reaches a scripted video
        we have already passed rather than reporting failure while one exists — the same
        wrap ``step`` navigates with.  The video on screen is never the answer:
        "next" has to move, and reloading it would read as the command having
        restarted the video for nothing.
        """
        entry = self._next_funscripted_entry()
        if entry is None:
            self._notices.say("no other funscripted video", level=NoticeLevel.WARNING)
            return
        index, video = entry
        self._session.load(index)
        # Read the funscript off the session rather than parsing it again: the
        # load just built it, and this is the one that will actually drive.
        funscript = self._session.current_funscript
        onset = None if funscript is None else funscript.first_real_event_ms
        if onset is not None:
            # None means the action starts promptly, and the video already does.
            self._session.seek_to(onset)
        logger.info("Next funscripted: %s", video.name)
        self._notices.say("next funscripted", level=NoticeLevel.HIGHLIGHT)

    def _next_funscripted_entry(self):
        """``(index, video)`` of the next scripted entry, or None if we are it."""
        playlist = self._session.playlist
        current = self._session.index
        for offset in range(1, len(playlist) + 1):
            index = (current + offset) % len(playlist)
            video, funscript = playlist[index]
            if funscript is not None and index != current:
                return index, video
        return None
