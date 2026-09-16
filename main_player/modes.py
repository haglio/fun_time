"""The modes the main player is playing in, and what changes them.

Three of them, and they are not the same kind of thing.  The *length mode* is
the library's own filter — mixed, shorts, full — and changing it rebuilds the
playlist.  The *compilation* is one anthology's clips standing in for the
playlist, which :mod:`main_player.clip_jumps` owns because entering one is what puts you
there.
*F-mode* is Fun Time's filter over whichever of those is running, and the main player cannot
see it: the narrowed playlist it receives is indistinguishable from any other,
so the flag has to be said outright for the HUD to be able to show it.

They are gathered here because the console draws them as one line and the mode
memory writes them down as one record, and because the two ways out of a
compilation — naming a length, or leaving without naming one — both need the
length that was feeding the playlist when the compilation was entered.

Lived as four closures over two ``nonlocal``s inside ``main_player.app``'s run loop.
"""
from __future__ import annotations

import logging

from player_core.console_hud import ModeHud

from .library import NONE
from .library_source import DEFAULT_MODE, LENGTH_MODES, length_mode_rebuilds, next_length_mode
from .mode_memory import RememberedMode
from .status import LibraryStatus

logger = logging.getLogger(__name__)


def reload_playlist(session, jumps, resolve) -> None:
    """Take up a playlist that was rewritten under this player.

    Fun Time owns the playlist file and rewrites it whenever the room's
    selection changes; RELOAD_PLAYLIST is it saying so, and *resolve* reads the
    new list.

    Replaced rather than loaded, so the video on screen carries on: only what
    "next" reaches has changed.  The compilation is dropped with it, because
    the playlist is no longer the one a compilation put there.
    """
    session.replace_playlist(resolve())
    jumps.leave_compilation()


class Modes:
    """What this player is playing, as the console says it and the memory keeps it."""

    def __init__(self, source, session, jumps, *, remembered: str) -> None:
        self._source = source
        self._session = session
        self._jumps = jumps
        # Empty when there is no library backing the playlist (Fun Time can hand
        # a main player one without library dirs): no length filter is running, so the HUD
        # has no mode to name and the toggle has nothing to rebuild.
        self._length_mode = (remembered or DEFAULT_MODE) if source is not None else ""
        # Defaults off, because a session that is never told is a session where
        # nothing narrowed it.
        self._f_mode = False

    @property
    def length_mode(self) -> str:
        """The library filter feeding the playlist, or "" with no library."""
        return self._length_mode

    @property
    def f_mode(self) -> bool:
        """Whether Fun Time says it narrowed this playlist to the scripted videos."""
        return self._f_mode

    def set_f_mode(self, on: bool) -> None:
        self._f_mode = on

    def set_length(self, mode: str) -> None:
        """Play *mode*'s videos, if that asks for anything.

        Naming the mode already running asks for nothing, and the rebuild it
        would trigger is not nothing: the playlist is reshuffled and landed on
        at entry 0, so saying "mixed" twice puts two different videos on screen.
        Inside a compilation the same words do have work, and are the point.

        Asking for neither length leaves the video on screen and locks it, since
        there is no list to move on to.  The playlist it had is kept, so putting
        a length back plays from a rebuild rather than from nothing.
        """
        if self._source is None:
            return
        mode = mode.strip().lower()
        if mode not in LENGTH_MODES:
            return
        if not length_mode_rebuilds(mode, self.length_mode,
                                    in_compilation=bool(self._jumps.compilation)):
            return
        self._length_mode = mode
        self._jumps.leave_compilation()
        logger.info("Length mode: %s", mode)
        if mode == NONE:
            self._session.set_locked(True)
            return
        self._session.load_playlist(self._source.playlist_for(mode))

    def toggle_length(self) -> None:
        """The next mode in the cycle, from the one in force now."""
        self.set_length(next_length_mode(self.length_mode))

    def end_compilation(self) -> None:
        """Out of a compilation without naming a length.

        The mode that was feeding the playlist when the compilation was entered
        is the one still held here, since PLAY_COMPILATION replaces the playlist
        but not the mode.  The clip on screen keeps playing — leaving is about
        what "next" reaches.
        """
        if self._source is None:
            return
        self._jumps.end_compilation(self._source.playlist_for(self.length_mode))

    @property
    def hud(self) -> ModeHud:
        """What the console's top block says about what is playing."""
        return ModeHud(
            video=self._name_on_screen,
            length_mode=self.length_mode,
            compilation=self._jumps.compilation,
            position=self._session.index + 1,
            total=len(self._session.playlist),
            f_mode=self.f_mode,
        )

    @property
    def _name_on_screen(self) -> str:
        session = self._session
        if not session.switching_versions and session.on_default_version:
            return self._jumps.title
        return f"{self._jumps.title} ({session.current_video.name})"

    @property
    def library_status(self) -> LibraryStatus:
        return LibraryStatus(
            length_mode=self.length_mode,
            compilation=self._jumps.compilation,
            has_compilation=self._jumps.has_compilation,
            has_other_versions=self._session.has_other_versions,
            jump_to=self._jumps.jump_to,
        )

    @property
    def remembered(self) -> RememberedMode:
        """What the next session needs, since a list of files cannot say it."""
        return RememberedMode(
            length_mode=self.length_mode,
            compilation=self._jumps.compilation,
            # Only while inside one: the clip is the compilation's anchor, and
            # outside a compilation there is nothing to anchor.
            video=str(self._session.current_video) if self._jumps.compilation else "",
        )
