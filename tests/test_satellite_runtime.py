"""The satellite's verbs, driven through its registry one at a time."""
from __future__ import annotations

import threading

from player_core.player_verbs import (
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    PLAY_FILE,
    PREV,
    QUIT,
    RELOAD_PLAYLIST,
    SET_PACE,
    TRASH,
)

from satellite.runtime import VERBS, SatelliteControls, apply_command
from tests.satellite_fakes import make_satellite_session


def _never_reloads() -> None:
    """The reload hook, for the verbs that must not reach it."""
    raise AssertionError("RELOAD_PLAYLIST was not the command under test")


def _controls(tmp_path, *, entries=3, **wired) -> SatelliteControls:
    session, _player = make_satellite_session(tmp_path, entries=entries)
    return SatelliteControls(session, **{"reload_playlist": _never_reloads, **wired})


class TestApplyCommand:
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


def test_every_verb_the_satellite_answers_is_spelled_by_the_family():
    """A satellite has no verbs of its own: everything it answers is a verb any
    player may be sent, so a spelling here that player_verbs does not carry is a
    control that drifted."""
    from player_core import player_verbs

    assert set(VERBS) == {
        NEXT, PREV, LOCK_ON, LOCK_OFF, TRASH, PLAY_FILE, RELOAD_PLAYLIST, SET_PACE, QUIT}
    assert all(getattr(player_verbs, verb) == verb for verb in VERBS)
