"""The modes Kino is in, what changes them, and what they change about the playlist.

Three modes walk in a cycle -- mixed, shorts, full -- and each rebuild reshuffles
the library and lands on the first entry, which is why "the mode already running"
has to be a no-op and why "inside a compilation" is the one case where it is not.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from player_core.playback import Playback
from player_core.playlist import PlaylistItem

from main_player.library_source import FULL, MIXED, SHORTS
from main_player.mode_memory import RememberedMode
from main_player.modes import Modes, reload_playlist
from main_player.versions import Versions
from tests.satellite_fakes import FakeSatellitePlayer

FIRST = Path("videos/Jane Doe - scene one.mp4")
SECOND = Path("videos/Ann Bly - scene two.mp4")


def _built_for(mode: str) -> list[PlaylistItem]:
    return [PlaylistItem(Path(f"videos/{mode}-1.mp4")), PlaylistItem(Path(f"videos/{mode}-2.mp4"))]


class FakeSource:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def playlist_for(self, mode: str) -> list[tuple[Path, Path | None]]:
        self.asked.append(mode)
        return _built_for(mode)


class FakePlayback:
    """Records the playlist moves a mode change drives, in the Funestra's shape.

    ``load_playlist`` restarts on the new list's first entry and
    ``replace_playlist`` keeps the video on screen; which of the two a change
    uses is the whole difference between naming a length and leaving a volume.
    """

    def __init__(self, current: Path = FIRST) -> None:
        self.current_video = current
        self.showing = current
        self.switching_versions = False
        self.index = 0
        self.playlist: list[Path] = [FIRST, SECOND]
        self.loaded: list[list[tuple[Path, Path | None]]] = []
        self.replaced: list[list[tuple[Path, Path | None]]] = []
        self.locks: list[bool] = []

    def load_playlist(self, playlist, funscripts=None) -> None:
        funscripts = funscripts or {}
        self.loaded.append([(path, funscripts.get(path)) for path in playlist])
        self.playlist = list(playlist)

    def replace_playlist(self, playlist, funscripts=None) -> None:
        funscripts = funscripts or {}
        self.replaced.append([(path, funscripts.get(path)) for path in playlist])
        self.playlist = list(playlist)

    def set_locked(self, locked: bool) -> None:
        self.locks.append(locked)


class FakeVersions:
    def __init__(self, *, has_other_versions: bool = False, on_default: bool = True) -> None:
        self.has_other_versions = has_other_versions
        self.on_default = on_default


class FakeJumps:
    def __init__(self, compilation: str = "", *, has_compilation: bool = False,
                 jump_to: str = "", title: str = "") -> None:
        self.compilation = compilation
        self.title = title
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
    playback = FakePlayback(current)
    jumps = FakeJumps(compilation, title=title or current.stem)
    return Modes(source, playback, jumps, FakeVersions(), remembered=remembered), playback, jumps, source


class TestWhatModeThisSessionOpensIn:
    def test_it_opens_in_the_mode_the_last_session_wrote_down(self):
        modes, _playback, _jumps, _source = _modes(remembered=SHORTS)

        assert modes.length_mode == SHORTS

    def test_a_session_that_was_never_told_opens_in_the_default(self):
        modes, _playback, _jumps, _source = _modes(remembered="")

        assert modes.length_mode == MIXED

    def test_with_no_library_behind_the_playlist_there_is_no_mode_to_name(self):
        modes = Modes(None, FakePlayback(), FakeJumps(), FakeVersions(), remembered=SHORTS)

        assert modes.length_mode is None


class TestNamingALength:
    def test_it_rebuilds_the_playlist_from_the_library_and_plays_from_its_top(self):
        modes, playback, _jumps, source = _modes(remembered=MIXED)

        modes.set_length(SHORTS)

        assert modes.length_mode == SHORTS
        assert source.asked == [SHORTS]
        assert playback.loaded == [_built_for(SHORTS)]

    def test_naming_the_mode_already_running_asks_for_nothing(self):
        modes, playback, _jumps, source = _modes(remembered=MIXED)

        modes.set_length(MIXED)

        assert (playback.loaded, source.asked) == ([], [])

    def test_inside_a_compilation_the_same_words_are_the_way_out(self):
        modes, playback, jumps, _source = _modes(remembered=MIXED, compilation="Vol6")

        modes.set_length(MIXED)

        assert len(playback.loaded) == 1
        assert jumps.left == 1

    def test_a_rebuild_from_the_library_leaves_the_volume_behind(self):
        modes, _playback, jumps, _source = _modes(remembered=MIXED, compilation="Vol6")

        modes.set_length(FULL)

        assert jumps.left == 1

    def test_asking_for_neither_length_holds_the_video_on_screen(self):
        """There is no list to move on to, so the video is locked where it is;
        the list it had is kept for a length put back later."""
        modes, playback, _jumps, source = _modes(remembered=MIXED)

        modes.set_length("none")

        assert playback.locks == [True]
        assert (playback.loaded, source.asked) == ([], [])

    @pytest.mark.parametrize("said", ["SHORTS", " shorts ", "Shorts"])
    def test_the_mode_is_read_however_it_was_said(self, said):
        modes, _playback, _jumps, _source = _modes(remembered=MIXED)

        modes.set_length(said)

        assert modes.length_mode == SHORTS

    @pytest.mark.parametrize("said", ["", "medium", "shorts full"])
    def test_a_length_the_library_does_not_have_changes_nothing(self, said):
        modes, playback, _jumps, _source = _modes(remembered=MIXED)

        modes.set_length(said)

        assert (modes.length_mode, playback.loaded) == (MIXED, [])

    def test_with_no_library_there_is_nothing_to_rebuild_from(self):
        playback = FakePlayback()
        modes = Modes(None, playback, FakeJumps(), FakeVersions(), remembered=MIXED)

        modes.set_length(SHORTS)

        assert (modes.length_mode, playback.loaded) == (None, [])

    def test_a_length_the_library_has_no_videos_for_leaves_the_list_alone(self):
        class EmptySource(FakeSource):
            def playlist_for(self, mode: str):
                self.asked.append(mode)
                return []

        modes, playback, _jumps, _source = _modes(remembered=MIXED, source=EmptySource())

        modes.set_length(SHORTS)

        assert playback.loaded == []


class TestTogglingTheLength:
    @pytest.mark.parametrize("from_mode, to_mode",
                             [(MIXED, SHORTS), (SHORTS, FULL), (FULL, MIXED)])
    def test_it_walks_the_cycle_and_wraps(self, from_mode, to_mode):
        modes, _playback, _jumps, _source = _modes(remembered=from_mode)

        modes.toggle_length()

        assert modes.length_mode == to_mode

    def test_toggling_twice_keeps_walking_rather_than_flipping_back(self):
        modes, _playback, _jumps, _source = _modes(remembered=MIXED)

        modes.toggle_length()
        modes.toggle_length()

        assert modes.length_mode == FULL


class TestLeavingACompilationWithoutNamingALength:
    def test_the_mode_still_held_is_what_next_reaches(self):
        modes, _playback, jumps, source = _modes(remembered=FULL, compilation="Vol6")

        modes.end_compilation()

        assert source.asked == [FULL]
        assert jumps.ended_with == [_built_for(FULL)]

    def test_the_clip_on_screen_keeps_playing(self):
        modes, playback, _jumps, _source = _modes(remembered=FULL, compilation="Vol6")

        modes.end_compilation()

        assert playback.loaded == []

    def test_with_no_library_there_is_no_playlist_to_come_back_to(self):
        jumps = FakeJumps("Vol6")
        modes = Modes(None, FakePlayback(), jumps, FakeVersions(), remembered=MIXED)

        modes.end_compilation()

        assert jumps.ended_with == []


class TestFunTimesOwnFilter:
    def test_it_defaults_off_because_a_session_never_told_is_one_nothing_narrowed(self):
        modes, _playback, _jumps, _source = _modes()

        assert modes.scripted_filter is False

    def test_being_told_is_the_only_way_it_goes_on(self):
        modes, _playback, _jumps, _source = _modes()

        modes.set_scripted_filter(True)

        assert modes.scripted_filter is True


class TestWhatTheConsoleIsToldToDraw:
    def test_it_names_the_video_the_mode_and_the_place_in_the_playlist(self):
        modes, playback, _jumps, _source = _modes(remembered=SHORTS)
        playback.index = 1

        hud = modes.hud

        assert (hud.video, hud.length_mode) == ("Jane Doe - scene one", SHORTS)
        assert (hud.position, hud.total) == (2, 2)

    def test_the_name_is_the_librarys_record_of_the_video_not_its_filename(self):
        modes, _playback, _jumps, _source = _modes(title="Jane Doe - Alpha Study: Part Two")

        assert modes.hud.video == "Jane Doe - Alpha Study: Part Two"

    def test_the_volume_is_named_while_inside_one(self):
        modes, _playback, _jumps, _source = _modes(compilation="Vol6")

        assert modes.hud.compilation == "Vol6"

    def test_fun_times_filter_is_said_outright_because_nothing_else_shows_it(self):
        modes, _playback, _jumps, _source = _modes()
        modes.set_scripted_filter(True)

        assert modes.hud.scripted_filter is True


class TestTheFileNamedBesideAVideoWithVersions:
    DEFAULT = "Jane-Doe_720-q7Rk2w.mp4"
    UPSCALE = "Jane-Doe_720-q7Rk2w_topaz.mp4"
    TITLE = "Jane Doe - scene one"

    def _switchable(self, tmp_path) -> tuple[Modes, Playback, Versions]:
        default, upscale, other = (
            tmp_path / name for name in (self.DEFAULT, self.UPSCALE, "Ann-Bly_540-z3Jm8d.mp4"))
        for path in (default, upscale, other):
            path.write_text("x")
        family = [PlaylistItem(default), PlaylistItem(upscale)]
        playback = Playback([default, other], player=FakeSatellitePlayer(), locked=True)
        versions = Versions(playback, {default: family, upscale: family})
        return Modes(None, playback, FakeJumps(title=self.TITLE), versions, remembered=""), playback, versions

    def test_from_the_first_version_switch_on_every_press_names_the_file(self, tmp_path):
        modes, _playback, versions = self._switchable(tmp_path)
        said = [modes.hud.video]

        for _ in range(2):
            versions.cycle(1)
            said.append(modes.hud.video)

        assert said == [
            self.TITLE,
            f"{self.TITLE} ({self.UPSCALE})",
            f"{self.TITLE} ({self.DEFAULT})",
        ]

    def test_come_back_to_later_it_names_the_file_only_off_the_default_version(self, tmp_path):
        modes, playback, versions = self._switchable(tmp_path)
        said = []

        for _ in range(2):
            versions.cycle(1)
            playback.step(1)
            playback.step(-1)
            said.append(modes.hud.video)

        assert said == [f"{self.TITLE} ({self.UPSCALE})", self.TITLE]


class TestWhatIsPublishedAboutThePlaceInTheLibrary:
    def test_the_versions_say_whether_the_version_button_has_anything_to_do(self):
        source, playback = FakeSource(), FakePlayback()
        modes = Modes(source, playback, FakeJumps("Vol6", jump_to="scene"),
                      FakeVersions(has_other_versions=True), remembered=SHORTS)

        library = modes.library_status

        assert (library.length_mode, library.compilation) == (SHORTS, "Vol6")
        assert (library.has_compilation, library.has_other_versions, library.jump_to) == (True, True, "scene")


class TestWhatIsWrittenDownForTheNextSession:
    def test_it_carries_the_length_mode(self):
        modes, _playback, _jumps, _source = _modes(remembered=FULL)

        assert modes.remembered == RememberedMode(length_mode=FULL)

    def test_inside_a_compilation_the_clip_is_the_anchor_too(self):
        modes, _playback, _jumps, _source = _modes(remembered=FULL, compilation="Vol6",
                                                   current=SECOND)

        assert modes.remembered == RememberedMode(
            length_mode=FULL, compilation="Vol6", video=str(SECOND))

    def test_outside_one_there_is_no_volume_to_anchor(self):
        modes, _playback, _jumps, _source = _modes(remembered=FULL, current=SECOND)

        assert modes.remembered.video == ""

    def test_it_follows_a_mode_change_so_the_next_session_opens_where_this_left(self):
        modes, _playback, _jumps, _source = _modes(remembered=MIXED)

        modes.toggle_length()

        assert modes.remembered.length_mode == SHORTS


class TestTakingUpAPlaylistFunTimeRewrote:
    def test_the_new_list_arrives_without_interrupting_the_video(self):
        playback, jumps = FakePlayback(), FakeJumps()

        reload_playlist(playback, jumps, lambda: _built_for(FULL))

        assert playback.replaced == [_built_for(FULL)]
        assert playback.loaded == []

    def test_the_volume_is_left_behind(self):
        playback, jumps = FakePlayback(), FakeJumps("Vol6")

        reload_playlist(playback, jumps, lambda: _built_for(FULL))

        assert jumps.left == 1

    def test_an_empty_list_leaves_the_video_and_the_list_alone(self):
        playback, jumps = FakePlayback(), FakeJumps()

        reload_playlist(playback, jumps, list)

        assert playback.replaced == []
