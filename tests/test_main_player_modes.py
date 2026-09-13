"""The modes the main player is in, what changes them, and what they change about the playlist.

Three modes walk in a cycle — mixed, shorts, full — and each rebuild reshuffles
the library and lands on the first entry, which is why "the mode already running"
has to be a no-op and why "inside a compilation" is the one case where it is not.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from player_core.playlist import PlaylistItem

from main_player.library_source import FULL, MIXED, SHORTS
from main_player.mode_memory import RememberedMode
from main_player.modes import Modes, reload_playlist
from main_player.session import PlayerSession
from tests.test_main_player_session import FakePlayer, FakeTCode

FIRST = Path("videos/Jane Doe - scene one.mp4")
SECOND = Path("videos/Ann Bly - scene two.mp4")


def _built_for(mode: str) -> list[tuple[Path, Path | None]]:
    """What the fake library hands back for *mode* — named, so a case can say
    which mode's playlist it expected rather than asking the fake again."""
    return [(Path(f"videos/{mode}-1.mp4"), None), (Path(f"videos/{mode}-2.mp4"), None)]


class FakeSource:
    """The library backing the playlist: it can build a list for any mode."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def playlist_for(self, mode: str) -> list[tuple[Path, Path | None]]:
        self.asked.append(mode)
        return _built_for(mode)


class FakeSession:
    """Records the playlist moves a mode change drives.

    ``load_playlist`` restarts on the new list's first entry and
    ``replace_playlist`` keeps the video on screen; which of the two a change
    uses is the whole difference between naming a length and leaving a volume.
    """

    def __init__(self, current: Path = FIRST, *, has_other_versions: bool = False) -> None:
        self.current_video = current
        self.has_other_versions = has_other_versions
        self.switching_versions = False
        self.on_default_version = True
        self.index = 0
        self.playlist: list[tuple[Path, Path | None]] = [(FIRST, None), (SECOND, None)]
        self.loaded: list[list[tuple[Path, Path | None]]] = []
        self.replaced: list[list[tuple[Path, Path | None]]] = []

    def load_playlist(self, playlist) -> None:
        self.loaded.append(list(playlist))
        self.playlist = list(playlist)

    def replace_playlist(self, playlist) -> None:
        self.replaced.append(list(playlist))
        self.playlist = list(playlist)


class FakeJumps:
    """Where in a compilation this player is, and the two ways out of one."""

    def __init__(self, compilation: str = "", *, has_compilation: bool = False,
                 jump_to: str = "", title: str = "") -> None:
        self.compilation = compilation
        # What the video on screen is called: the library's record of it where
        # there is one, and the real ClipJumps falls back to the filename, so
        # this is never empty in a running player.
        self.title = title
        # What the console's own compilation and clip/scene buttons can do from
        # the video on screen — the player's answers, since only it has the
        # library to look in.
        self.has_compilation = has_compilation or bool(compilation)
        self.jump_to = jump_to
        self.left = 0
        self.ended_with: list[list[tuple[Path, Path | None]]] = []

    def leave_compilation(self) -> None:
        self.left += 1
        self.compilation = ""

    def end_compilation(self, playlist) -> None:
        self.ended_with.append(list(playlist))
        self.compilation = ""


def _modes(*, remembered: str = MIXED, source: FakeSource | None = None,
           compilation: str = "", current: Path = FIRST, title: str = ""):
    source = FakeSource() if source is None else source
    session = FakeSession(current)
    jumps = FakeJumps(compilation, title=title or current.stem)
    return Modes(source, session, jumps, remembered=remembered), session, jumps, source


class TestWhatModeThisSessionOpensIn:
    def test_it_opens_in_the_mode_the_last_session_wrote_down(self):
        modes, _session, _jumps, _source = _modes(remembered=SHORTS)

        assert modes.length_mode == SHORTS

    def test_a_session_that_was_never_told_opens_in_the_default(self):
        modes, _session, _jumps, _source = _modes(remembered="")

        assert modes.length_mode == MIXED

    def test_with_no_library_behind_the_playlist_there_is_no_mode_to_name(self):
        """Fun Time can hand the main player a playlist without library dirs.  No length
        filter is running then, so the HUD names none and the toggle has
        nothing to rebuild."""
        modes = Modes(None, FakeSession(), FakeJumps(), remembered=SHORTS)

        assert modes.length_mode is None


class TestNamingALength:
    def test_it_rebuilds_the_playlist_from_the_library(self):
        modes, session, _jumps, source = _modes(remembered=MIXED)

        modes.set_length(SHORTS)

        assert modes.length_mode == SHORTS
        assert source.asked == [SHORTS]
        assert session.loaded == [_built_for(SHORTS)]

    def test_naming_the_mode_already_running_asks_for_nothing(self):
        """The rebuild is not nothing -- the playlist is reshuffled and landed
        on at entry 0 -- so saying "mixed" twice would put two different videos
        on screen.  Fun Time's reset says it on every press, which made a
        control meaning "put it back" the quickest way to keep changing what
        was playing."""
        modes, session, _jumps, source = _modes(remembered=MIXED)

        modes.set_length(MIXED)

        assert (session.loaded, source.asked) == ([], [])

    def test_inside_a_compilation_the_same_words_are_the_way_out(self):
        """PLAY_COMPILATION swaps the playlist for one volume's clips without
        touching the mode, so an unchanged mode is exactly the case that must
        rebuild there."""
        modes, session, jumps, _source = _modes(remembered=MIXED, compilation="Vol6")

        modes.set_length(MIXED)

        assert len(session.loaded) == 1
        assert jumps.left == 1

    def test_a_rebuild_from_the_library_leaves_the_volume_behind(self):
        modes, _session, jumps, _source = _modes(remembered=MIXED, compilation="Vol6")

        modes.set_length(FULL)

        assert jumps.left == 1

    @pytest.mark.parametrize("said", ["SHORTS", " shorts ", "Shorts"])
    def test_the_mode_is_read_however_it_was_said(self, said):
        """It arrives from a spoken command, from a dashboard button, and from
        the L key, and none of those agree on case."""
        modes, _session, _jumps, _source = _modes(remembered=MIXED)

        modes.set_length(said)

        assert modes.length_mode == SHORTS

    @pytest.mark.parametrize("said", ["", "medium", "shorts full"])
    def test_a_length_the_library_does_not_have_changes_nothing(self, said):
        modes, session, _jumps, _source = _modes(remembered=MIXED)

        modes.set_length(said)

        assert (modes.length_mode, session.loaded) == (MIXED, [])

    def test_with_no_library_there_is_nothing_to_rebuild_from(self):
        session = FakeSession()
        modes = Modes(None, session, FakeJumps(), remembered=MIXED)

        modes.set_length(SHORTS)

        assert (modes.length_mode, session.loaded) == (None, [])


class TestTogglingTheLength:
    @pytest.mark.parametrize("from_mode, to_mode",
                             [(MIXED, SHORTS), (SHORTS, FULL), (FULL, MIXED)])
    def test_it_walks_the_cycle_and_wraps(self, from_mode, to_mode):
        modes, _session, _jumps, _source = _modes(remembered=from_mode)

        modes.toggle_length()

        assert modes.length_mode == to_mode

    def test_toggling_twice_keeps_walking_rather_than_flipping_back(self):
        """The step is taken from the mode in force *now*, so a toggle bound to
        the mode this player opened in would alternate between two forever."""
        modes, _session, _jumps, _source = _modes(remembered=MIXED)

        modes.toggle_length()
        modes.toggle_length()

        assert modes.length_mode == FULL


class TestLeavingACompilationWithoutNamingALength:
    def test_the_mode_still_held_is_what_next_reaches(self):
        """PLAY_COMPILATION replaces the playlist but not the mode, so the mode
        that was feeding it when the volume was entered is still the one here."""
        modes, _session, jumps, source = _modes(remembered=FULL, compilation="Vol6")

        modes.end_compilation()

        assert source.asked == [FULL]
        assert jumps.ended_with == [_built_for(FULL)]

    def test_the_clip_on_screen_keeps_playing(self):
        """Leaving is about what "next" reaches, not about what is playing --
        which is why it goes through end_compilation rather than a rebuild."""
        modes, session, _jumps, _source = _modes(remembered=FULL, compilation="Vol6")

        modes.end_compilation()

        assert session.loaded == []

    def test_with_no_library_there_is_no_playlist_to_come_back_to(self):
        jumps = FakeJumps("Vol6")
        modes = Modes(None, FakeSession(), jumps, remembered=MIXED)

        modes.end_compilation()

        assert jumps.ended_with == []


class TestFunTimesOwnFilter:
    def test_it_defaults_off_because_a_session_never_told_is_one_nothing_narrowed(self):
        modes, _session, _jumps, _source = _modes()

        assert modes.scripted_filter is False

    def test_being_told_is_the_only_way_it_goes_on(self):
        """F-mode narrows the playlist Fun Time writes to the scripted videos,
        and the result is indistinguishable from any other playlist here."""
        modes, _session, _jumps, _source = _modes()

        modes.set_scripted_filter(True)

        assert modes.scripted_filter is True


class TestWhatTheConsoleIsToldToDraw:
    def test_it_names_the_video_the_mode_and_the_place_in_the_playlist(self):
        modes, session, _jumps, _source = _modes(remembered=SHORTS)
        session.index = 1

        hud = modes.hud

        assert (hud.video, hud.length_mode) == ("Jane Doe - scene one", SHORTS)
        assert (hud.position, hud.total) == (2, 2)

    def test_the_name_is_the_librarys_record_of_the_video_not_its_filename(self):
        """The muted line under the status is where the viewer reads what is
        playing, and a download's filename is the worst name the library has
        for it -- so the player says what Evolver recorded it as instead."""
        modes, _session, _jumps, _source = _modes(title="Jane Doe - Alpha Study: Part Two")

        assert modes.hud.video == "Jane Doe - Alpha Study: Part Two"

    def test_the_volume_is_named_while_inside_one(self):
        modes, _session, _jumps, _source = _modes(compilation="Vol6")

        assert modes.hud.compilation == "Vol6"

    def test_fun_times_filter_is_said_outright_because_nothing_else_shows_it(self):
        modes, _session, _jumps, _source = _modes()
        modes.set_scripted_filter(True)

        assert modes.hud.scripted_filter is True


class TestTheFileNamedBesideAVideoWithVersions:
    DEFAULT = "Jane-Doe_720-q7Rk2w.mp4"
    UPSCALE = "Jane-Doe_720-q7Rk2w_topaz.mp4"
    TITLE = "Jane Doe - scene one"

    def _switchable(self, tmp_path) -> tuple[Modes, PlayerSession]:
        default, upscale, other = (
            tmp_path / name for name in (self.DEFAULT, self.UPSCALE, "Ann-Bly_540-z3Jm8d.mp4"))
        for path in (default, upscale, other):
            path.write_text("x")
        family = [PlaylistItem(default), PlaylistItem(upscale)]
        session = PlayerSession(
            [PlaylistItem(default), PlaylistItem(other)], player=FakePlayer(),
            tcode=FakeTCode(), version_index={default: family, upscale: family})
        return Modes(None, session, FakeJumps(title=self.TITLE), remembered=""), session

    def test_from_the_first_version_switch_on_every_press_names_the_file(self, tmp_path):
        modes, session = self._switchable(tmp_path)
        said = [modes.hud.video]

        for _ in range(2):
            session.cycle_version()
            said.append(modes.hud.video)

        assert said == [
            self.TITLE,
            f"{self.TITLE} ({self.UPSCALE})",
            f"{self.TITLE} ({self.DEFAULT})",
        ]

    def test_come_back_to_later_it_names_the_file_only_off_the_default_version(
            self, tmp_path):
        modes, session = self._switchable(tmp_path)
        said = []

        for _ in range(2):
            session.cycle_version()
            session.step(1)
            session.step(-1)
            said.append(modes.hud.video)

        assert said == [f"{self.TITLE} ({self.UPSCALE})", self.TITLE]


class TestWhatIsWrittenDownForTheNextSession:
    def test_it_carries_the_length_mode(self):
        modes, _session, _jumps, _source = _modes(remembered=FULL)

        assert modes.remembered == RememberedMode(length_mode=FULL)

    def test_inside_a_compilation_the_clip_is_the_anchor_too(self):
        """Fun Time can only rotate its resumed file onto a video the file
        contains, and a compilation's clips often are not in it -- so the clip
        is remembered and the volume comes back around it."""
        modes, _session, _jumps, _source = _modes(remembered=FULL, compilation="Vol6",
                                                  current=SECOND)

        assert modes.remembered == RememberedMode(
            length_mode=FULL, compilation="Vol6", video=str(SECOND))

    def test_outside_one_there_is_no_volume_to_anchor(self):
        modes, _session, _jumps, _source = _modes(remembered=FULL, current=SECOND)

        assert modes.remembered.video == ""

    def test_it_follows_a_mode_change_so_the_next_session_opens_where_this_left(self):
        modes, _session, _jumps, _source = _modes(remembered=MIXED)

        modes.toggle_length()

        assert modes.remembered.length_mode == SHORTS


class TestTakingUpAPlaylistFunTimeRewrote:
    """RELOAD_PLAYLIST: the room's selection changed and the file under this
    player is a different list now."""

    def test_the_new_list_arrives_without_interrupting_the_video(self):
        """Replaced rather than loaded: only what "next" reaches has changed,
        so the clip on screen carries on."""
        session, jumps = FakeSession(), FakeJumps()

        reload_playlist(session, jumps, lambda: _built_for(FULL))

        assert session.replaced == [_built_for(FULL)]
        assert session.loaded == []

    def test_the_volume_is_left_behind(self):
        """The playlist is no longer the one a compilation put there, so what
        the HUD says about being inside one would be a lie."""
        session, jumps = FakeSession(), FakeJumps("Vol6")

        reload_playlist(session, jumps, lambda: _built_for(FULL))

        assert jumps.left == 1

