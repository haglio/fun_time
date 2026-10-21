"""The modes Kino is in, and what it is told about the list it plays.

The list is Fun Time's: it narrows and orders the Main player's one list and
tells Kino what the buttons built it under, since a narrowed list looks like any
other.  What Kino keeps of its own is the compilation, the one place it swaps
the list for a set of its own.
"""
from __future__ import annotations

from pathlib import Path

from funestra.playback import Playback
from funestra.playlist import PlaylistItem

from main_funestra.mode_memory import RememberedMode
from main_funestra.modes import Modes, reload_playlist
from main_funestra.versions import Versions
from tests.satellite_fakes import FakeSatelliteEngine

FIRST = Path("videos/Jane Doe - scene one.mp4")
SECOND = Path("videos/Ann Bly - scene two.mp4")
THE_MAIN_LIST = [PlaylistItem(Path("videos/full-1.mp4")), PlaylistItem(Path("videos/full-2.mp4"))]


class FakePlayback:
    """Records the playlist moves Kino drives, in the Funestra's shape.

    ``load_playlist`` restarts on the new list's first entry and
    ``replace_playlist`` keeps the video on screen.
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


def _modes(*, compilation: str = "", current: Path = FIRST, title: str = "",
           versions=None, the_main_list=lambda: THE_MAIN_LIST):
    playback = FakePlayback(current)
    jumps = FakeJumps(compilation, title=title or current.stem)
    modes = Modes(playback, jumps, versions or FakeVersions(), the_main_list=the_main_list)
    return modes, playback, jumps


class TestLeavingACompilationWithoutNamingALength:
    def test_the_main_list_fun_time_wrote_is_what_next_reaches(self):
        modes, _playback, jumps = _modes(compilation="Vol6")

        modes.end_compilation()

        assert jumps.ended_with == [THE_MAIN_LIST]

    def test_the_clip_on_screen_keeps_playing(self):
        modes, playback, _jumps = _modes(compilation="Vol6")

        modes.end_compilation()

        assert playback.loaded == []


class TestFunTimesOwnFilter:
    def test_it_defaults_off_because_a_session_never_told_is_one_nothing_narrowed(self):
        modes, _playback, _jumps = _modes()

        assert modes.scripted_filter is False

    def test_being_told_is_the_only_way_it_goes_on(self):
        modes, _playback, _jumps = _modes()

        modes.set_scripted_filter(True)

        assert modes.scripted_filter is True


class TestWhatTheConsoleIsToldToDraw:
    def test_it_names_the_video_and_the_place_in_the_playlist(self):
        modes, playback, _jumps = _modes()
        playback.index = 1

        hud = modes.hud

        assert hud.video == "Jane Doe - scene one"
        assert (hud.position, hud.total) == (2, 2)

    def test_the_name_is_the_librarys_record_of_the_video_not_its_filename(self):
        modes, _playback, _jumps = _modes(title="Jane Doe - Alpha Study: Part Two")

        assert modes.hud.video == "Jane Doe - Alpha Study: Part Two"

    def test_the_volume_is_named_while_inside_one(self):
        modes, _playback, _jumps = _modes(compilation="Vol6")

        assert modes.hud.compilation == "Vol6"

    def test_fun_times_filter_is_said_outright_because_nothing_else_shows_it(self):
        modes, _playback, _jumps = _modes()
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
        playback = Playback([default, other], engine=FakeSatelliteEngine(), locked=True)
        versions = Versions(playback, {default: family, upscale: family})
        modes = Modes(playback, FakeJumps(title=self.TITLE), versions, the_main_list=list)
        return modes, playback, versions

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
        modes = Modes(FakePlayback(), FakeJumps("Vol6", jump_to="scene"),
                      FakeVersions(has_other_versions=True), the_main_list=list)

        library = modes.library_status

        assert library.compilation == "Vol6"
        assert (library.has_compilation, library.has_other_versions, library.jump_to) == (True, True, "scene")


class TestWhatIsWrittenDownForTheNextSession:
    def test_inside_a_compilation_the_clip_is_its_anchor(self):
        modes, _playback, _jumps = _modes(compilation="Vol6", current=SECOND)

        assert modes.remembered == RememberedMode(compilation="Vol6", video=str(SECOND))

    def test_outside_one_there_is_no_volume_to_anchor(self):
        modes, _playback, _jumps = _modes(current=SECOND)

        assert modes.remembered == RememberedMode()


class TestTakingUpAPlaylistFunTimeRewrote:
    def test_the_new_list_arrives_without_interrupting_the_video(self):
        playback, jumps = FakePlayback(), FakeJumps()

        reload_playlist(playback, jumps, lambda: THE_MAIN_LIST)

        assert playback.replaced == [THE_MAIN_LIST]
        assert playback.loaded == []

    def test_the_volume_is_left_behind(self):
        playback, jumps = FakePlayback(), FakeJumps("Vol6")

        reload_playlist(playback, jumps, lambda: THE_MAIN_LIST)

        assert jumps.left == 1

    def test_an_empty_list_leaves_the_video_and_the_list_alone(self):
        playback, jumps = FakePlayback(), FakeJumps()

        reload_playlist(playback, jumps, list)

        assert playback.replaced == []
