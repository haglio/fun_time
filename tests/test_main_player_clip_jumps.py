"""The playlist moves a clip's sidecar makes possible, and the state one leaves."""
from __future__ import annotations

import json
from pathlib import Path

from main_player.clip_jumps import ClipJumps
from main_player.clip_nav import ClipNav


def _clip(lib: Path, meta: Path, rel: str, comp: str, index: int,
          source: str, performer: str) -> Path:
    video = lib / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")
    sidecar = (meta / video.relative_to(lib)).with_suffix(".json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({
        "clip": {"compilation": comp, "index": index,
                 "source": source, "performer": performer},
    }), encoding="utf-8")
    return video


class FakeSession:
    """Records the playlist moves the jumps drive."""

    def __init__(self, current: Path, playlist=None) -> None:
        self.current_video = current
        self.playlist = list(playlist or [(current, None)])
        self.replaced: list[list[tuple[Path, Path | None]]] = []
        self.played: list[tuple[Path, Path | None]] = []
        # A real session only restarts playback when it loads an index; swapping
        # the list around the clip on screen leaves this alone.
        self.loaded_first = 0

    def replace_playlist(self, playlist) -> None:
        self.replaced.append(list(playlist))
        self.playlist = list(playlist)

    def load_playlist(self, playlist) -> None:
        self.replaced.append(list(playlist))
        self.playlist = list(playlist)
        self.loaded_first += 1

    def play_file(self, video: Path, funscript: Path | None) -> None:
        self.played.append((video, funscript))


class FakeNotices:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def say(self, message: str, *, level: str = "error") -> bool:
        self.said.append((message, level))
        return True


def _world(tmp_path: Path):
    """A Vol6 compilation of two clips, plus the scene one was taken from."""
    lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    first = _clip(lib, meta, "w/Jane Doe - Scene Two.mp4", "Vol6", 1,
                  "Scene Two", "Jane Doe")
    second = _clip(lib, meta, "w/Ann Bly - Alpha Scene 2.mp4", "Vol6", 9,
                   "Alpha Scene 2", "Ann Bly")
    scene = lib / "other" / "Alpha Scene 2 - Ann Bly 1080p.mp4"
    scene.parent.mkdir(parents=True, exist_ok=True)
    scene.write_bytes(b"x")
    nav = ClipNav.build([first, second, scene], meta)
    return nav, first, second, scene


def _jumps(nav, current: Path, funscripts=None, playlist=None):
    session = FakeSession(current, playlist)
    notices = FakeNotices()
    return ClipJumps(nav, session, funscripts or {}, notices), session, notices


class TestWhatTheButtonsCanDoFromHere:
    """The console draws its compilation and clip/scene buttons dim where a press
    would do nothing, and only this player can say which case it is in."""

    def test_a_clip_says_it_belongs_to_a_compilation(self, tmp_path):
        nav, first, _second, scene = _world(tmp_path)

        assert _jumps(nav, first)[0].has_compilation is True
        assert _jumps(nav, scene)[0].has_compilation is False

    def test_a_clip_says_its_scene_is_reachable_and_a_scene_says_its_clip_is(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)

        assert _jumps(nav, second)[0].jump_to == "scene"
        assert _jumps(nav, scene)[0].jump_to == "clip"

    def test_a_clip_whose_scene_is_not_in_the_library_says_nothing(self, tmp_path):
        """The common case — most clips' source scenes were never kept."""
        nav, first, _second, _scene = _world(tmp_path)

        assert _jumps(nav, first)[0].jump_to == ""

    def test_the_library_is_walked_once_per_video_rather_than_per_ask(self, tmp_path):
        """The console asks several times a second, and each answer walks the
        clips against the scenes."""
        nav, _first, second, scene = _world(tmp_path)
        walks: list[Path] = []

        class Counting:
            def __init__(self, real):
                self._real = real

            def compilation_of(self, video):
                return self._real.compilation_of(video)

            def full_vid_of(self, video):
                walks.append(video)
                return self._real.full_vid_of(video)

            def clip_of(self, video):
                return self._real.clip_of(video)

        jumps, session, _notices = _jumps(Counting(nav), second)
        for _ in range(5):
            assert jumps.jump_to == "scene"
        assert len(walks) == 1

        session.current_video = scene
        assert jumps.jump_to == "clip"
        assert len(walks) == 2


class TestResume:
    """Reopening cannot be read off the playlist.  Fun Time rotates the resumed
    file onto the video its player last showed, but only when that video is in
    the file — and a compilation's clips often are not, so the list comes back
    leading with something else entirely.  The remembered clip is the anchor."""

    def test_the_remembered_volume_is_rebuilt_around_the_remembered_clip(self, tmp_path):
        nav, first, second, scene = _world(tmp_path)
        # The resumed playlist leads with a video from the ordinary browse, and
        # does not contain the clip that was on screen at all.
        jumps, session, _notices = _jumps(nav, scene, playlist=[(scene, None)])

        jumps.resume("Vol6", second)

        assert jumps.compilation == "Vol6"
        assert session.played == [(second, None)]
        assert [video for video, _fs in session.replaced[-1]] == [first, second]

    def test_a_volume_the_remembered_clip_is_not_in_is_dropped(self, tmp_path):
        """Something rebuilt rather than resumed, and the remembered pair belongs
        to a session that is over."""
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _notices = _jumps(nav, scene)

        jumps.resume("Vol10", second)

        assert jumps.compilation == ""
        assert session.replaced == []
        assert session.played == []

    def test_nothing_remembered_resumes_to_nothing(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _notices = _jumps(nav, scene)

        jumps.resume("", second)
        jumps.resume("Vol6", None)

        assert jumps.compilation == ""
        assert session.replaced == []

    def test_a_remembered_non_clip_resumes_to_nothing(self, tmp_path):
        nav, _first, _second, scene = _world(tmp_path)
        jumps, session, _notices = _jumps(nav, scene)

        jumps.resume("Vol6", scene)

        assert jumps.compilation == ""
        assert session.replaced == []


class TestEndCompilation:
    def test_the_video_on_screen_keeps_playing(self, tmp_path):
        """Leaving is about what "next" will reach, not about what is playing —
        the clip on screen carries on, now in the length mode's playlist."""
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _notices = _jumps(nav, second)
        jumps.play_compilation()
        browse = [(second, None), (scene, None)]

        jumps.end_compilation(browse)

        assert jumps.compilation == ""
        assert session.replaced[-1] == browse
        assert session.loaded_first == 0  # nothing reloaded, so nothing restarted

    def test_a_clip_the_length_mode_filtered_out_is_kept_anyway(self, tmp_path):
        """A quarter of a volume's clips are non-canonical versions and so are
        absent from the mode's own playlist.  Dropping them would mean leaving a
        compilation yanked the video away — which is exactly what leaving must
        not do."""
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, _notices = _jumps(nav, second)
        jumps.play_compilation()
        without_it = [(scene, None)]

        jumps.end_compilation(without_it)

        assert session.replaced[-1] == [(second, None), (scene, None)]
        assert session.loaded_first == 0

    def test_outside_a_compilation_it_does_nothing(self, tmp_path):
        nav, _first, _second, scene = _world(tmp_path)
        jumps, session, _notices = _jumps(nav, scene)

        jumps.end_compilation([(scene, None)])

        assert session.replaced == []


class TestPlayCompilation:
    def test_swaps_the_playlist_for_the_volume_in_order(self, tmp_path):
        nav, first, second, _scene = _world(tmp_path)
        jumps, session, notices = _jumps(nav, second)

        jumps.play_compilation()

        assert [video for video, _fs in session.replaced[0]] == [first, second]
        assert notices.said == [("compilation: 2 clips", "notice")]

    def test_remembers_which_compilation_is_holding_the_playlist(self, tmp_path):
        """The HUD reports this; it is the state the player could get stuck in
        with nothing on screen naming it."""
        nav, _first, second, _scene = _world(tmp_path)
        jumps, _session, _notices = _jumps(nav, second)

        assert jumps.compilation == ""
        jumps.play_compilation()
        assert jumps.compilation == "Vol6"

    def test_a_non_clip_says_so_and_leaves_the_playlist_alone(self, tmp_path):
        nav, _first, _second, scene = _world(tmp_path)
        jumps, session, notices = _jumps(nav, scene)

        jumps.play_compilation()

        assert session.replaced == []
        assert notices.said == [("not a compilation clip", "error")]
        assert jumps.compilation == ""

    def test_leaving_forgets_it(self, tmp_path):
        """Saying "shorts" or reloading rebuilds the playlist from elsewhere, so
        the compilation is no longer what is on screen."""
        nav, _first, second, _scene = _world(tmp_path)
        jumps, _session, _notices = _jumps(nav, second)
        jumps.play_compilation()

        jumps.leave_compilation()

        assert jumps.compilation == ""


class TestSingleVideoJumps:
    def test_full_vid_plays_the_scene_the_clip_came_from(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        script = tmp_path / "scene.funscript"
        jumps, session, notices = _jumps(nav, second, {scene: script})

        jumps.play_full_vid()

        assert session.played == [(scene, script)]
        assert notices.said == [("full video", "notice")]

    def test_clip_jump_plays_the_clip_carved_from_the_scene(self, tmp_path):
        nav, _first, second, scene = _world(tmp_path)
        jumps, session, notices = _jumps(nav, scene)

        jumps.play_clip_jump()

        assert session.played == [(second, None)]
        assert notices.said == [("clip jump", "notice")]

    def test_a_miss_says_so_rather_than_moving(self, tmp_path):
        """Most clips' source movies are not in the library at all, so having
        nowhere to go is the ordinary case — and silence is what left the player
        looking broken."""
        nav, first, _second, _scene = _world(tmp_path)
        jumps, session, notices = _jumps(nav, first)

        jumps.play_full_vid()

        assert session.played == []
        assert notices.said == [("full video not available", "error")]

    def test_neither_jump_disturbs_the_compilation_it_is_inside(self, tmp_path):
        """These two move to one video; only "compilation" replaces the playlist,
        so the HUD must keep naming the volume you are still walking."""
        nav, _first, second, _scene = _world(tmp_path)
        jumps, _session, _notices = _jumps(nav, second)
        jumps.play_compilation()

        jumps.play_full_vid()

        assert jumps.compilation == "Vol6"
