"""The playlist moves a clip's sidecar makes possible.

:mod:`main_player.clip_nav` reads the ``clip`` metadata Evolver records and answers
questions about one video; this drives the playlist from those answers, which is
what Fun Time's "compilation" / "full video" / "clip jump" actually do.

Playing a compilation replaces the whole playlist, so it is a place you can be
*stuck* — unlike the other two, which move to one video and leave the playlist
alone.  That is the state the main player's HUD reports, so it lives here, with whatever
puts you in it.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ClipJumps:
    """Fun Time's three clip navigations, over one session's playlist."""

    def __init__(self, nav, session, funscripts: dict[Path, Path | None], notices) -> None:
        self._nav = nav
        self._session = session
        self._funscripts = funscripts
        self._notices = notices
        self._compilation = ""
        # What the three navigations could do from the video on screen, worked
        # out once per video.  The console asks on every publish and the answers
        # walk the library's clips against its scenes, which is not a thing to do
        # several times a second for a video that has not changed.
        self._asked_about: Path | None = None
        self._reachable: tuple[bool, str] = (False, "")

    @property
    def compilation(self) -> str:
        """The compilation whose clips are the playlist, or "" while browsing."""
        return self._compilation

    @property
    def has_compilation(self) -> bool:
        """Whether the video on screen belongs to a compilation at all.

        What says the console's button can be pressed, where :attr:`compilation`
        says you are already inside one.
        """
        return self._reach()[0]

    @property
    def jump_to(self) -> str:
        """Where a clip/scene jump would go from here: "scene" from a clip to
        the scene it was cut from, "clip" the other way, "" from a video that is
        neither.  Most clips' source scenes are not in the library, so "" is the
        common answer."""
        return self._reach()[1]

    def _reach(self) -> tuple[bool, str]:
        current = self._session.current_video
        if current != self._asked_about:
            self._asked_about = current
            self._reachable = (
                bool(self._nav.compilation_of(current)),
                "scene" if self._nav.full_vid_of(current) is not None
                else "clip" if self._nav.clip_of(current) is not None else "",
            )
        return self._reachable

    def resume(self, compilation: str, video: Path | None) -> None:
        """Come back to *video* inside *compilation*, when the two still agree.

        Entering a compilation swaps the playlist in memory only, so the file Fun
        Time resumes never learns of it.  Fun Time rotates that file onto the
        video its player last showed — but only when the video is *in* the file,
        and a compilation's clips often are not, so the list comes back leading
        with something else entirely.  The remembered clip is therefore the
        anchor: play it, and build the compilation around it.  A clip that does
        not belong to the remembered compilation is from a session that is over.
        """
        if not compilation or video is None:
            return
        if self._nav.compilation_of(video) != compilation:
            return
        self._session.play_file(video, self._funscripts.get(video))
        self._enter(video)

    def leave_compilation(self) -> None:
        """Note that the playlist was rebuilt from somewhere else — the library's
        length modes and Fun Time's reload both do that, and either way the
        compilation is no longer what is on screen."""
        self._compilation = ""

    def end_compilation(self, playlist: list[tuple[Path, Path | None]]) -> None:
        """Leave the compilation for *playlist*, without interrupting the video.

        Leaving is about what "next" will reach, not about what is playing, so
        the clip on screen carries on — it is simply no longer surrounded by its
        siblings.  (Naming a length mode is the other way out, and that one does
        land on the new mode's content, which is the point of asking for it.)
        """
        if not self._compilation:
            return
        self._compilation = ""
        current = self._session.current_video
        if current not in {video for video, _fs in playlist}:
            # A quarter of a compilation's clips are non-canonical versions of
            # their group, so the mode's own playlist does not carry them.
            # Letting one of those fall out would make leaving yank the video
            # away, which is the one thing leaving must not do.
            playlist = [(current, self._funscripts.get(current)), *playlist]
        self._session.replace_playlist(playlist)

    def play_compilation(self) -> None:
        """Reorder the playlist to just the current clip's compilation, in order."""
        current = self._session.current_video
        if not self._enter(current):
            self._notices.say("not a compilation clip", level="warning")
            return
        self._notices.say(
            f"compilation: {len(self._session.playlist)} clips", level="notice")

    def _enter(self, current: Path) -> bool:
        """Put *current*'s compilation in the playlist around it, if it has one.

        The clip on screen keeps playing: it survives into the new list, so the
        session follows it rather than restarting at the compilation's first clip.
        """
        siblings = self._nav.compilation_playlist(current)
        if not siblings:
            return False
        self._session.replace_playlist(
            [(video, self._funscripts.get(video)) for video in siblings]
        )
        self._compilation = self._nav.compilation_of(current)
        return True

    def play_full_vid(self) -> None:
        """Jump from the current clip to the library scene it was taken from."""
        self._jump(self._nav.full_vid_of(self._session.current_video), "full video")

    def play_clip_jump(self) -> None:
        """Jump from the current full scene to its clip (the reverse of full vid)."""
        self._jump(self._nav.clip_of(self._session.current_video), "clip jump")

    def _jump(self, target: Path | None, what: str) -> None:
        """Play *target*, or say *what* had nowhere to go.

        A miss is the common case, not an error: most clips' source movies are not
        in the library at all, so the notice is the whole answer.
        """
        if target is None:
            logger.info("%s: nothing matches %s", what, self._session.current_video.name)
            self._notices.say(f"{what} not available", level="warning")
            return
        self._session.play_file(target, self._funscripts.get(target))
        self._notices.say(what, level="notice")
