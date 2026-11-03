from __future__ import annotations

from pathlib import Path

from funestra.modes import MainMode, SatellitesMode

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.origenerator_switches import RfbMode
from fun_time.session_resume import resume_shared_state
from fun_time.shared_state import (
    SHARED_STATE_FILENAME,
    BridgeState,
    migrate_shared_state,
    read_shared_state,
    write_shared_state,
)

_LAST_SESSIONS_STATE = (
    "[state]\nmain_mode = hybrid\nsatellites_mode = player\nmain_f_mode = 1\n"
    "omni_paused = 0\nmain_latest = 0\nmain_plays_vr = 1\nmain_plays_flat = 1\n"
    "genau_latest = 0\nactive_side = 3\nvolume = 40\nmuted = 1\n"
    "locked2 = 1\nportrait_filter = alpha\nportrait_f_mode = 1\nportrait_latest = 0\n"
    "portrait_loop = seed\nportrait_map_anchor = C:/v/a.mp4\nportrait_widen_video = \n"
    "portrait_nav_anchor = \nlocked3 = 0\nlandscape_filter = \nlandscape_f_mode = 0\n"
    "landscape_latest = 1\nlandscape_loop = \nlandscape_map_anchor = \n"
    "landscape_widen_video = \nlandscape_nav_anchor = \n"
)


class TestTheSharedStateFile:
    def test_a_file_in_last_sessions_spelling_comes_back_as_the_same_session(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        assert migrate_shared_state(state_file) is True

        state = read_shared_state(state_file)
        assert state.satellite(FunTimeFunestra.PORTRAIT).locked is True
        assert state.satellite(FunTimeFunestra.LANDSCAPE).locked is False
        assert state.active_funestra == 3
        assert state.main_scripted_filter is True
        assert state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter is True
        assert state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter is False
        assert (state.volume, state.muted) == (40, True)
        assert state.satellite(FunTimeFunestra.PORTRAIT).filter == "alpha"
        assert state.satellite(FunTimeFunestra.LANDSCAPE).latest is True

    def test_the_mode_selector_under_the_log_keeps_its_mode_under_its_new_key(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text("[state]\nrfb_slot = origenerator\n", encoding="utf-8")

        migrate_shared_state(state_file)

        assert read_shared_state(state_file).rfb_mode is RfbMode.ORIGENERATOR

    def test_the_old_keys_are_gone_from_the_file_afterwards(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        migrate_shared_state(state_file)

        text = state_file.read_text(encoding="utf-8")
        for old in ("locked2", "locked3", "active_side", "f_mode"):
            assert old not in text, old

    def test_a_mode_word_this_app_has_dropped_comes_back_as_kino(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        migrate_shared_state(state_file)

        state = read_shared_state(state_file)
        assert (state.main_mode, *(state.satellite(funestra).mode
                                   for funestra in FunTimeFunestra.SATELLITES), state.rfb_mode) == (
            MainMode.KINO, SatellitesMode.KINO, SatellitesMode.KINO, RfbMode.RFB)

    def test_a_room_left_in_origenerator_mode_comes_back_with_all_three_in_it(
            self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text("[state]\nsatellites_mode = origenerator\n", encoding="utf-8")

        migrate_shared_state(state_file)

        state = read_shared_state(state_file)
        assert [state.satellite(funestra).mode for funestra in FunTimeFunestra.SATELLITES] == [
            SatellitesMode.ORIGENERATOR, SatellitesMode.ORIGENERATOR]
        assert state.rfb_mode is RfbMode.ORIGENERATOR
        assert "satellites_mode" not in state_file.read_text(encoding="utf-8")

    def test_a_file_already_in_todays_spelling_is_left_untouched(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        write_shared_state(state_file, BridgeState(active_funestra=2))
        before = state_file.read_text(encoding="utf-8")

        assert migrate_shared_state(state_file) is False

        assert state_file.read_text(encoding="utf-8") == before

    def test_no_file_means_nothing_to_rewrite(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME

        assert migrate_shared_state(state_file) is False

        assert not state_file.exists()

    def test_a_resumed_session_reads_last_sessions_file_through_the_rewrite(self, tmp_path: Path):
        state_file = tmp_path / SHARED_STATE_FILENAME
        state_file.write_text(_LAST_SESSIONS_STATE, encoding="utf-8")

        state = resume_shared_state(state_file, resumed=True)

        assert state.satellite(FunTimeFunestra.PORTRAIT).locked is True
        assert state.main_scripted_filter is True
        assert "locked2" not in state_file.read_text(encoding="utf-8")

