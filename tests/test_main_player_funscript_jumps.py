"""Getting to the scripted parts: past this video's lull, or on to a video
that has scripting at all."""
from __future__ import annotations

from pathlib import Path

from player_core.funscript import Funscript

from main_player.funscript_jumps import FunscriptJumps

# Two runs of dense action with a long quiet stretch between them — the shape
# these jumps exist for.
_TWO_RUNS = Funscript(actions=[
    (10000, 0), (10300, 100), (10600, 0),
    (40000, 0), (40300, 100), (40600, 0),
])
# Action from the top: nothing to skip forward to, and nothing to skip into
# when a video like this is the one landed on.
_PROMPT = Funscript(actions=[(0, 0), (300, 100), (600, 0), (900, 100)])
# A long quiet lead-in, then action — what "next funscripted" has to land past.
_LATE = Funscript(actions=[(60000, 0), (60300, 100), (60600, 0)])


class FakeSession:
    """Records the moves the jumps drive, over a fixed playlist."""

    def __init__(self, playlist, *, index: int = 0, funscripts=None) -> None:
        self.playlist = list(playlist)
        self.index = index
        self.position_ms = 0.0
        self.seeks: list[float] = []
        self.loads: list[int] = []
        # Which Funscript each playlist entry's script parses to, so loading an
        # entry publishes it exactly as the real session's load does.
        self._funscripts = funscripts or {}

    @property
    def current_funscript(self):
        return self._funscripts.get(self.playlist[self.index][1])

    def load(self, index: int) -> None:
        self.index = index % len(self.playlist)
        self.loads.append(self.index)
        self.position_ms = 0.0

    def seek_to(self, position_ms: float) -> None:
        self.seeks.append(position_ms)
        self.position_ms = position_ms


class FakeNotices:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def say(self, message: str, *, level: str) -> bool:
        self.said.append((message, level))
        return True


def _jumps(session):
    notices = FakeNotices()
    return FunscriptJumps(session, notices), notices


def _scripted(tmp_path: Path, name: str) -> tuple[Path, Path]:
    return tmp_path / f"{name}.mp4", tmp_path / f"{name}.funscript"


class TestJumpToFunscript:
    def test_a_lull_is_skipped_to_where_the_action_resumes(self, tmp_path):
        video, script = _scripted(tmp_path, "alpha")
        session = FakeSession([(video, script)], funscripts={script: _TWO_RUNS})
        session.position_ms = 25000.0
        jumps, notices = _jumps(session)

        jumps.jump_to_funscript()

        assert session.seeks == [40000]
        # "favorite", not "notice": Fun Time flashes that level green, which is
        # what it reserves for the favorites and the funscripts.
        assert notices.said == [("funscript jump", "favorite")]

    def test_the_quiet_lead_in_counts_as_a_lull(self, tmp_path):
        video, script = _scripted(tmp_path, "alpha")
        session = FakeSession([(video, script)], funscripts={script: _LATE})
        jumps, _notices = _jumps(session)

        jumps.jump_to_funscript()

        assert session.seeks == [60000]

    def test_nothing_scripted_ahead_says_so_and_stays_put(self, tmp_path):
        video, script = _scripted(tmp_path, "alpha")
        session = FakeSession([(video, script)], funscripts={script: _TWO_RUNS})
        session.position_ms = 50000.0  # past the last run
        jumps, notices = _jumps(session)

        jumps.jump_to_funscript()

        assert session.seeks == []
        assert notices.said == [("no funscripting ahead", "warning")]

    def test_an_unscripted_video_has_nowhere_to_jump(self, tmp_path):
        video = tmp_path / "beta.mp4"
        session = FakeSession([(video, None)])
        jumps, notices = _jumps(session)

        jumps.jump_to_funscript()

        assert session.seeks == []
        assert notices.said == [("no funscripting ahead", "warning")]

    def test_it_never_goes_backward_into_the_run_already_playing(self, tmp_path):
        """Mid-run, "next" is the run after this one — a jump that landed on the
        start of the run you are inside would rewind, which is not a jump."""
        video, script = _scripted(tmp_path, "alpha")
        session = FakeSession([(video, script)], funscripts={script: _TWO_RUNS})
        session.position_ms = 10300.0
        jumps, _notices = _jumps(session)

        jumps.jump_to_funscript()

        assert session.seeks == [40000]


class TestNextFunscripted:
    def test_skips_the_unscripted_videos_between_here_and_the_next_script(self, tmp_path):
        video0, script0 = _scripted(tmp_path, "alpha")
        video1 = tmp_path / "beta.mp4"
        video2, script2 = _scripted(tmp_path, "gamma")
        session = FakeSession(
            [(video0, script0), (video1, None), (video2, script2)],
            funscripts={script0: _TWO_RUNS, script2: _LATE},
        )
        jumps, notices = _jumps(session)

        jumps.next_funscripted()

        assert session.loads == [2]
        assert notices.said == [("next funscripted", "favorite")]

    def test_lands_where_the_new_video_s_action_begins(self, tmp_path):
        video0 = tmp_path / "alpha.mp4"
        video1, script1 = _scripted(tmp_path, "beta")
        session = FakeSession(
            [(video0, None), (video1, script1)], funscripts={script1: _LATE},
        )
        jumps, _notices = _jumps(session)

        jumps.next_funscripted()

        assert session.seeks == [60000]

    def test_a_video_scripted_from_the_top_is_not_seeked_into(self, tmp_path):
        """The action already starts where the video does, so there is nothing to
        skip — and seeking anyway would drop the first cycles."""
        video0 = tmp_path / "alpha.mp4"
        video1, script1 = _scripted(tmp_path, "beta")
        session = FakeSession(
            [(video0, None), (video1, script1)], funscripts={script1: _PROMPT},
        )
        jumps, _notices = _jumps(session)

        jumps.next_funscripted()

        assert session.loads == [1]
        assert session.seeks == []

    def test_wraps_past_the_end_to_reach_a_script_behind_us(self, tmp_path):
        video0, script0 = _scripted(tmp_path, "alpha")
        video1 = tmp_path / "beta.mp4"
        video2 = tmp_path / "gamma.mp4"
        session = FakeSession(
            [(video0, script0), (video1, None), (video2, None)],
            index=1, funscripts={script0: _LATE},
        )
        jumps, _notices = _jumps(session)

        jumps.next_funscripted()

        assert session.loads == [0]

    def test_the_video_on_screen_is_never_the_answer(self, tmp_path):
        """The only script in the playlist is the one already playing, so "next"
        has nowhere to go — reloading it would read as the command restarting the
        video for nothing."""
        video0 = tmp_path / "alpha.mp4"
        video1, script1 = _scripted(tmp_path, "beta")
        session = FakeSession(
            [(video0, None), (video1, script1)], index=1,
            funscripts={script1: _TWO_RUNS},
        )
        jumps, notices = _jumps(session)

        jumps.next_funscripted()

        assert session.loads == []
        assert session.seeks == []
        assert notices.said == [("no other funscripted video", "warning")]

    def test_an_entirely_unscripted_playlist_says_so(self, tmp_path):
        session = FakeSession([
            (tmp_path / "alpha.mp4", None), (tmp_path / "beta.mp4", None),
        ])
        jumps, notices = _jumps(session)

        jumps.next_funscripted()

        assert session.loads == []
        assert notices.said == [("no other funscripted video", "warning")]
