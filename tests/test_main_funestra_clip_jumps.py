from __future__ import annotations

import json
from pathlib import Path

from funestra.modes import MessageLevel
from funestra.playlist import PlaylistItem

from main_funestra.clip_jumps import ClipJumps
from main_funestra.clip_nav import ClipNav
from tests.clip_records import ClipRecord


def _clip(lib: Path, meta: Path, rel: str, record: ClipRecord) -> Path:
    video = lib / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")
    sidecar = (meta / video.relative_to(lib)).with_suffix(".json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"clip": record.recorded()}), encoding="utf-8")
    return video


class FakePlayback:
    def __init__(self, current: Path, playlist=None) -> None:
        self.current_video = current
        self.playlist = [item.path for item in (playlist or [PlaylistItem(current)])]
        self.replaced: list[list[PlaylistItem]] = []
        self.played: list[tuple[Path, Path | None]] = []
        self.restarts = 0

    def replace_playlist(self, playlist, funscripts=None) -> None:
        funscripts = funscripts or {}
        self.replaced.append([PlaylistItem(path, funscripts.get(path)) for path in playlist])
        self.playlist = list(playlist)
        if self.current_video not in self.playlist:
            self.restarts += 1

    def load_playlist(self, playlist, funscripts=None) -> None:
        self.replace_playlist(playlist, funscripts)
        self.restarts += 1

    def play_file(self, video, funscript=None) -> None:
        self.played.append((video, funscript))
        self.current_video = video


class FakeMessages:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def say(self, message: str, *, level: str) -> bool:
        self.said.append((message, level))
        return True


def _world(tmp_path: Path):
    lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    first = _clip(lib, meta, "w/Jane Doe - Scene Two.mp4", ClipRecord("Vol6", 1, "Scene Two", "Jane Doe"))
    second = _clip(lib, meta, "w/Ann Bly - Alpha Scene 2.mp4", ClipRecord("Vol6", 9, "Alpha Scene 2", "Ann Bly"))
    scene = lib / "other" / "Alpha Scene 2 - Ann Bly 1080p.mp4"
    scene.parent.mkdir(parents=True, exist_ok=True)
    scene.write_bytes(b"x")
    nav = ClipNav.build([first, second, scene], meta)
    return nav, first, second, scene


def _jumps(nav, current: Path, funscripts=None, playlist=None):
    playback = FakePlayback(current, playlist)
    messages = FakeMessages()
    return ClipJumps(nav, playback, funscripts or {}, messages), playback, messages


class CountingNav:
    def __init__(self, real) -> None:
        self._real = real
        self.walks: list[tuple[str, Path]] = []

    def _walk_the_library(self, name: str, video: Path):
        self.walks.append((name, video))
        return getattr(self._real, name)(video)

    def compilation_of(self, video):
        return self._walk_the_library("compilation_of", video)

    def full_vid_of(self, video):
        return self._walk_the_library("full_vid_of", video)

    def clip_of(self, video):
        return self._walk_the_library("clip_of", video)

    def title_of(self, video):
        return self._walk_the_library("title_of", video)


class TestWhatTheButtonsCanDoFromHere:
    def test_a_clip_says_it_belongs_to_a_compilation(self, tmp_path):
        nav, first, _second, scene = _world(tmp_path)

        assert _jumps(nav, first)[0].has_compilation is True
        assert _jumps(nav, scene)[0].has_compilation is False

    def test_a_clip_says_its_scene_is_reachable_and_a_scene_says_its_clip_is(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)

        assert _jumps(nav, second)[0].jump_to == "scene"
        assert _jumps(nav, scene)[0].jump_to == "clip"

    def test_the_usual_clip_whose_scene_was_never_kept_offers_no_jump(self, tmp_path):
        nav, first, _second, _scene = _world(tmp_path)

        assert _jumps(nav, first)[0].jump_to == ""

    def test_the_library_is_walked_once_per_video_however_often_the_hud_asks(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        counting = CountingNav(nav)

        jumps, session, _messages = _jumps(counting, second)
        for _ in range(5):
            assert jumps.jump_to == "scene"
        assert [video for name, video in counting.walks if name == "full_vid_of"] == [second]

        session.current_video = scene
        assert jumps.jump_to == "clip"
        assert [video for name, video in counting.walks if name == "full_vid_of"] == [second, scene]


class TestResume:
    def test_the_remembered_volume_is_rebuilt_around_the_remembered_clip(self, tmp_path):
        nav, first, second, scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, scene, playlist=[PlaylistItem(scene)])

        jumps.resume("Vol6", second)

        assert jumps.compilation == "Vol6"
        assert session.played == [(second, None)]
        assert [video for video, _fs in session.replaced[-1]] == [first, second]

    def test_a_pair_from_before_the_library_was_rebuilt_is_dropped_not_resumed(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, scene)

        jumps.resume("Vol10", second)

        assert jumps.compilation == ""
        assert session.replaced == []
        assert session.played == []

    def test_nothing_remembered_resumes_to_nothing(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, scene)

        jumps.resume("", second)
        jumps.resume("Vol6", None)

        assert jumps.compilation == ""
        assert session.replaced == []

    def test_a_remembered_non_clip_resumes_to_nothing(self, tmp_path):
        nav, _first, _second, scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, scene)

        jumps.resume("Vol6", scene)

        assert jumps.compilation == ""
        assert session.replaced == []


class TestEndCompilation:
    def test_the_video_on_screen_keeps_playing(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, second)
        jumps.play_compilation()
        browse = [PlaylistItem(second), PlaylistItem(scene)]

        jumps.end_compilation(browse)

        assert jumps.compilation == ""
        assert session.replaced[-1] == browse
        assert session.restarts == 0

    def test_a_clip_the_main_list_dropped_for_its_bigger_version_is_kept_anyway(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        bigger_version = _clip(lib, meta, "w/Ann Bly - Alpha Scene 2_apo8_iris2.mp4",
                               ClipRecord("Vol6", 9, "Alpha Scene 2", "Ann Bly"))
        bigger_version.write_bytes(b"x" * 400)
        jumps, session, _messages = _jumps(nav, second)
        jumps.play_compilation()
        one_version_per_family = [PlaylistItem(bigger_version), PlaylistItem(scene)]

        jumps.end_compilation(one_version_per_family)

        assert session.replaced[-1] == [(second, None), (bigger_version, None), (scene, None)]
        assert session.restarts == 0

    def test_outside_a_compilation_it_does_nothing(self, tmp_path):
        nav, _first, _second, scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, scene)

        jumps.end_compilation([PlaylistItem(scene)])

        assert session.replaced == []


class TestPlayCompilation:
    def test_swaps_the_playlist_for_the_volume_in_order(self, tmp_path):
        nav, first, second, _scene = _world(tmp_path)
        jumps, session, messages = _jumps(nav, second)

        jumps.play_compilation()

        assert [video for video, _fs in session.replaced[0]] == [first, second]
        assert messages.said == [("compilation: 2 clips", MessageLevel.NORMAL)]

    def test_remembers_which_compilation_is_holding_the_playlist(self, tmp_path):
        nav, _first, second, _scene = _world(tmp_path)
        jumps, _session, _messages = _jumps(nav, second)

        assert jumps.compilation == ""
        jumps.play_compilation()
        assert jumps.compilation == "Vol6"

    def test_a_non_clip_says_so_and_leaves_the_playlist_alone(self, tmp_path):
        nav, _first, _second, scene = _world(tmp_path)
        jumps, session, messages = _jumps(nav, scene)

        jumps.play_compilation()

        assert session.replaced == []
        assert messages.said == [("not a compilation clip", MessageLevel.WARNING)]
        assert jumps.compilation == ""

    def test_leaving_forgets_it(self, tmp_path):
        nav, _first, second, _scene = _world(tmp_path)
        jumps, _session, _messages = _jumps(nav, second)
        jumps.play_compilation()

        jumps.leave_compilation()

        assert jumps.compilation == ""


class TestSingleVideoJumps:
    def test_full_vid_plays_the_scene_the_clip_came_from(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        script = tmp_path / "scene.funscript"
        jumps, session, messages = _jumps(nav, second, {scene: script})

        jumps.play_full_vid()

        assert session.played == [(scene, script)]
        assert messages.said == [("full video", MessageLevel.NORMAL)]

    def test_clip_jump_plays_the_clip_carved_from_the_scene(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, messages = _jumps(nav, scene)

        jumps.play_clip_jump()

        assert session.played == [(second, None)]
        assert messages.said == [("clip jump", MessageLevel.NORMAL)]

    def test_a_miss_is_said_as_a_warning_never_the_error_red(self, tmp_path):
        nav, first, _second, _scene = _world(tmp_path)
        jumps, session, messages = _jumps(nav, first)

        jumps.play_full_vid()

        assert session.played == []
        assert messages.said == [("full video not available", MessageLevel.WARNING)]

    def test_a_jump_to_one_video_replaces_no_list_so_the_volume_stays_named(self, tmp_path):
        nav, _first, second, _scene = _world(tmp_path)
        jumps, session, _messages = _jumps(nav, second)
        jumps.play_compilation()
        the_volumes_list = session.replaced[-1]

        jumps.play_full_vid()
        jumps.play_clip_jump()

        assert session.replaced[-1] is the_volumes_list
        assert jumps.compilation == "Vol6"


class TestWhatTheVideoIsCalled:
    def test_it_is_the_name_evolver_recorded_for_the_video(self, tmp_path):
        nav, first, _second, _scene = _world(tmp_path)

        assert _jumps(nav, first)[0].title == "Jane Doe - Scene Two"

    def test_the_name_rides_the_walk_the_buttons_already_paid_for(self, tmp_path):
        nav, _first, second, _scene = _world(tmp_path)
        counting = CountingNav(nav)

        jumps, _session, _messages = _jumps(counting, second)
        for _ in range(5):
            assert jumps.title == "Ann Bly - Alpha Scene 2"
            assert jumps.jump_to == "scene"
        assert [name for name, _video in counting.walks].count("title_of") == 1
