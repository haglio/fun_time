"""The session state file, rewritten once from last session's key spelling.

The keys were renamed on 2026-09-13, and the file on his machine was written
under the old ones.  It is rewritten at the startup that first reads it, and only
the new spelling is ever read -- the reader knows no other.
"""
from __future__ import annotations

from pathlib import Path

from player_core.modes import MainMode, SatellitesMode

from fun_time.players import Player
from fun_time.session_resume import resume_shared_state
from fun_time.shared_state import (
    SHARED_STATE_FILENAME,
    BridgeState,
    migrate_shared_state,
    read_shared_state,
    write_shared_state,
)

# The state file as a session of that day wrote it: the two locks by slot
# number, the active player as a side, one F-mode name for two filters, and two
# mode words the app has since dropped.
_LAST_SESSIONS_STATE = (
    "[state]\nmain_mode = hybrid\nsatellites_mode = player\nmain_f_mode = 1\n"
    "omni_paused = 0\nmain_latest = 0\nmain_plays_vr = 1\nmain_plays_flat = 1\n"
    "genau_latest = 0\nactive_side = 3\nvolume = 40\nmuted = 1\n"
    "locked2 = 1\nportrait_filter = alpha\nportrait_f_mode = 1\nportrait_latest = 0\n"
    "portrait_loop = seed\nportrait_map_anchor = C:/v/a.mp4\nportrait_widen_clip = \n"
    "portrait_nav_anchor = \nlocked3 = 0\nlandscape_filter = \nlandscape_f_mode = 0\n"
    "landscape_latest = 1\nlandscape_loop = \nlandscape_map_anchor = \n"
    "landscape_widen_clip = \nlandscape_nav_anchor = \n"
)




class TestTheSharedStateFile:
    def test_a_file_in_last_sessions_spelling_comes_back_as_the_same_session(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        assert migrate_shared_state(state_file) is True

        state = read_shared_state(state_file)
        assert state.satellite(Player.PORTRAIT).locked is True
        assert state.satellite(Player.LANDSCAPE).locked is False
        assert state.active_player == 3
        assert state.main_scripted_filter is True
        assert state.satellite(Player.PORTRAIT).favorites_filter is True
        assert state.satellite(Player.LANDSCAPE).favorites_filter is False
        assert (state.volume, state.muted) == (40, True)
        assert state.satellite(Player.PORTRAIT).filter == "alpha"
        assert state.satellite(Player.LANDSCAPE).latest is True

    def test_the_old_keys_are_gone_from_the_file_afterwards(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        migrate_shared_state(state_file)

        text = state_file.read_text(encoding="utf-8")
        for old in ("locked2", "locked3", "active_side", "f_mode"):
            assert old not in text, old

    def test_a_mode_word_this_app_has_dropped_comes_back_as_video(self, tmp_path: Path):
        """hybrid and player were mode words once.  The record reads a word it
        does not know as its default, so no table of old words is kept."""
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        migrate_shared_state(state_file)

        state = read_shared_state(state_file)
        assert (state.main_mode, state.satellites_mode) == (MainMode.VIDEO, SatellitesMode.VIDEO)

    def test_a_file_already_in_todays_spelling_is_left_untouched(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        write_shared_state(state_file, BridgeState(active_player=2))
        before = state_file.read_text(encoding="utf-8")

        assert migrate_shared_state(state_file) is False

        assert state_file.read_text(encoding="utf-8") == before

    def test_no_file_means_nothing_to_rewrite(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME

        assert migrate_shared_state(state_file) is False

        assert not state_file.exists()

    def test_a_resumed_session_reads_last_sessions_file_through_the_rewrite(self, tmp_path: Path):
        """Startup is where the file is first read, so it is where the rewrite
        happens: a session resumed over the old spelling comes back locked and
        narrowed the way it was left."""
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        state = resume_shared_state(state_file, resumed=True)

        assert state.satellite(Player.PORTRAIT).locked is True
        assert state.main_scripted_filter is True
        assert "locked2" not in state_file.read_text(encoding="utf-8")

