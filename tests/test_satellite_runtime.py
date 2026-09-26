"""The satellite's verbs, driven through its registry one at a time."""
from __future__ import annotations

import threading
from pathlib import Path

from player_core import player_verbs
from player_core.player_verbs import (
    CLEAR_FRAME,
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PLAY_FILE,
    PREV,
    QUIT,
    RELOAD_PLAYLIST,
    SET_PACE,
    SET_SPEED,
    SHOW_FRAME,
    SPEED_DOWN,
    SPEED_UP,
    TRASH,
)

from satellite.runtime import VERBS, SatelliteControls, apply_command
from satellite.versions import NEXT_VERSION, PREV_VERSION, step_version
from tests.satellite_fakes import make_satellite_session


def _never_reloads() -> None:
    """The reload hook, for the verbs that must not reach it."""
    raise AssertionError("RELOAD_PLAYLIST was not the command under test")


def _controls(tmp_path, *, entries=3, **wired) -> SatelliteControls:
    session, _player = make_satellite_session(tmp_path, entries=entries)
    return SatelliteControls(session, **{"reload_playlist": _never_reloads, **wired})


class TestApplyCommand:
    def test_show_frame_puts_that_picture_over_the_one_on_screen(self, tmp_path):
        controls = _controls(tmp_path)

        assert apply_command(f"{SHOW_FRAME} C:/frames/run one-3.png", controls) is True
        assert controls.session.frame == Path("C:/frames/run one-3.png")
        assert apply_command(CLEAR_FRAME, controls) is True
        assert controls.session.frame is None

    def test_set_pace_is_how_long_a_picture_holds_the_screen(self, tmp_path):
        session, player = make_satellite_session(tmp_path)
        controls = SatelliteControls(session, reload_playlist=_never_reloads)

        assert apply_command(f"{SET_PACE} 2.5", controls) is True
        assert player.pace_s == 2.5

    def test_next_and_prev_navigate(self, tmp_path):
        controls = _controls(tmp_path)
        assert apply_command(NEXT, controls) is True
        assert controls.session.current_video.name == "v1.mp4"
        assert apply_command(PREV, controls) is True
        assert controls.session.current_video.name == "v0.mp4"

    def test_the_hold_is_named_absolutely_as_every_player_names_it(self, tmp_path):
        """LOCK_ON and LOCK_OFF, the spellings the main slot's players answer:
        a satellite used to say LOCK and UNLOCK for the same hold."""
        controls = _controls(tmp_path)
        assert apply_command(LOCK_ON, controls) is True
        assert controls.session.is_locked is True
        assert apply_command(LOCK_OFF, controls) is True
        assert controls.session.is_locked is False

    def test_trash_discards_the_current_clip(self, tmp_path):
        controls = _controls(tmp_path)
        assert apply_command(TRASH, controls) is True
        assert [p.name for p in controls.session.playlist] == ["v1.mp4", "v2.mp4"]

    def test_play_file_plays_the_item_the_line_names(self, tmp_path):
        controls = _controls(tmp_path)
        assert apply_command(f"{PLAY_FILE} {tmp_path / 'v2.mp4'}", controls) is True
        assert controls.session.current_video == tmp_path / "v2.mp4"

    def test_play_file_drops_a_funscript_column_rather_than_taking_it_for_the_path(self, tmp_path):
        controls = _controls(tmp_path)
        line = f"{PLAY_FILE} {tmp_path / 'v2.mp4'}\t{tmp_path / 'v2.funscript'}"
        assert apply_command(line, controls) is True
        assert controls.session.current_video == tmp_path / "v2.mp4"

    def test_a_version_step_plays_the_rendition_the_family_names_next(self, tmp_path):
        controls = _controls(tmp_path)
        clip = controls.session.current_video
        other = tmp_path / "v0_sorted.mp4"
        other.write_text("fake")

        assert apply_command(step_version(1, [clip, other]), controls) is True
        assert controls.session.showing == other

        assert apply_command(step_version(-1, [clip, other]), controls) is True
        assert controls.session.showing == clip

    def test_two_quick_steps_carrying_one_family_both_land(self, tmp_path):
        """Fun Time reads the file on screen off a status file that lags the
        player, so it sends the family rather than a target: two presses read
        the same status, and a target would have put the same file up twice."""
        controls = _controls(tmp_path)
        clip = controls.session.current_video
        family = [clip, tmp_path / "v0_sorted.mp4", tmp_path / "v0_small.mp4"]
        for path in family[1:]:
            path.write_text("fake")

        for _ in range(2):
            apply_command(step_version(1, family), controls)

        assert controls.session.showing == family[2]

    def test_keyword_is_case_insensitive(self, tmp_path):
        controls = _controls(tmp_path)
        assert apply_command("next", controls) is True
        assert controls.session.current_video.name == "v1.mp4"

    def test_reload_playlist_invokes_the_callback(self, tmp_path):
        calls = []
        controls = _controls(tmp_path, reload_playlist=lambda: calls.append(1))
        assert apply_command(RELOAD_PLAYLIST, controls) is True
        assert calls == [1]

    def test_quit_sets_the_stop_event(self, tmp_path):
        stop = threading.Event()
        assert apply_command(QUIT, _controls(tmp_path, stop_event=stop)) is True
        assert stop.is_set()

    def test_a_build_with_no_stop_event_refuses_quit(self, tmp_path):
        """The headset's satellites end with the session, never on their own."""
        controls = _controls(tmp_path, stop_event=None)
        assert apply_command(QUIT, controls) is False

    def test_an_unknown_verb_is_refused_and_named_on_the_log(self, tmp_path, caplog):
        controls = _controls(tmp_path)
        with caplog.at_level("WARNING", logger="satellite.runtime"):
            assert apply_command("FLOOP", controls) is False
            assert apply_command("", controls) is False
        assert "FLOOP" in caplog.text

    def test_a_value_on_a_verb_that_takes_none_is_refused(self, tmp_path):
        """Half a command is not a command, and neither is one and a half — the
        same rule the main player and Genau already keep."""
        controls = _controls(tmp_path)
        assert apply_command(f"{NEXT} 5", controls) is False
        assert controls.session.current_video.name == "v0.mp4"

    def test_speed_up_and_down_move_the_rate_a_step_at_a_time(self, tmp_path):
        controls = _controls(tmp_path)

        assert apply_command(SPEED_UP, controls) is True
        assert controls.session.speed == 1.25
        assert apply_command(SPEED_DOWN, controls) is True
        assert controls.session.speed == 1.0

    def test_set_speed_takes_either_end_of_the_range_or_a_multiplier(self, tmp_path):
        controls = _controls(tmp_path)

        assert apply_command(f"{SET_SPEED} max", controls) is True
        assert controls.session.speed == 2.0
        assert apply_command(f"{SET_SPEED} min", controls) is True
        assert controls.session.speed == 0.25
        assert apply_command(f"{SET_SPEED} 1.5", controls) is True
        assert controls.session.speed == 1.5

    def test_a_set_speed_naming_no_rate_is_refused_and_leaves_the_rate_alone(self, tmp_path):
        controls = _controls(tmp_path)

        assert apply_command(f"{SET_SPEED} fast", controls) is False
        assert apply_command(SET_SPEED, controls) is False
        assert controls.session.speed == 1.0


def test_every_verb_the_satellite_answers_is_the_familys_or_its_own():
    """Everything a satellite answers is a verb any player may be sent, bar the
    pair about another version of the clip on screen: only this player answers
    those and only Fun Time sends them, so they are spelled beside its registry
    rather than in the family's vocabulary — which is where player_core's own
    rule leaves a name until a second repo needs it."""
    its_own = {NEXT_VERSION, PREV_VERSION}
    assert set(VERBS) == its_own | {
        NEXT, PREV, LOCK_ON, LOCK_OFF, TRASH, SPEED_UP, SPEED_DOWN, SET_SPEED,
        PLAY_FILE, RELOAD_PLAYLIST, SET_PACE, SHOW_FRAME, CLEAR_FRAME, QUIT,
    }
    assert all(getattr(player_verbs, verb) == verb for verb in set(VERBS) - its_own)
    assert not [verb for verb in its_own if hasattr(player_verbs, verb)]
