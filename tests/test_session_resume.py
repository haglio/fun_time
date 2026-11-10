from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
from typing import ClassVar

from funestra import funestra_verbs
from funestra.genau_controls import VERBS as GENAU_VERBS
from funestra.main_hud import OSR2_PARKED, OSR2_RETRACTED
from funestra.modes import MainMode

from fun_time.crown import Crown
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_settings import FunestraReports
from fun_time.funestra_status import MainFunestraStatus
from fun_time.main_list import MainList
from fun_time.origenerator_switches import RfbMode
from fun_time.session_resume import (
    DESCRIBES_THE_PLAYLIST,
    NOT_RESUMED,
    NOT_RESUMED_PER_SATELLITE,
    RESUMED_FIELDS,
    RESUMED_SATELLITE_FIELDS,
    open_the_main_list,
    playlist_opens_on,
    resume_genau,
    resume_main_lock,
    resume_main_loop,
    resume_playlists,
    resume_rates,
    resume_satellite_locks,
    resume_shared_state,
    resume_what_lives_in_a_funestra,
)
from fun_time.shared_state import BridgeState, SatelliteState, read_shared_state, write_shared_state
from main_funestra.genau_verbs import VERBS as GENAUS_VERBS_ON_THE_MAIN_FUNESTRA
from tests.made_up_state import remembered_values, shifted_state


def _videos(tmp_path: Path, *names: str) -> list[str]:
    paths = []
    for name in names:
        video = tmp_path / name
        video.write_bytes(b"")
        paths.append(str(video))
    return paths


def _write_playlist(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


class TestResumePlaylists:
    def test_rotating_onto_the_last_video_keeps_what_was_coming_up_coming_up(self, tmp_path: Path):
        a, b, c = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, b, c])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, c, a]

    def test_a_resumed_video_keeps_the_funscript_that_drives_the_osr2_through_it(self, tmp_path: Path):
        a, b, c = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [f"{a}\ta.funscript", b, f"{c}\tc.funscript"])

        resume_playlists([(playlist, c)])

        assert playlist.read_text(encoding="utf-8").splitlines() == [
            f"{c}\tc.funscript",
            f"{a}\ta.funscript",
            b,
        ]

    def test_reports_nothing_to_resume_when_a_playlist_file_is_missing(self, tmp_path: Path):
        assert resume_playlists([(tmp_path / "portrait_playlist.tsv", "")]) is False

    def test_the_satellites_playlists_resume_together_or_stay_as_the_build_left_them(self, tmp_path: Path):
        a, b = _videos(tmp_path, "a.mp4", "b.mp4")
        portrait = tmp_path / "portrait_playlist.tsv"
        _write_playlist(portrait, [a, b])

        resumed = resume_playlists([
            (portrait, b),
            (tmp_path / "landscape_playlist.tsv", ""),
        ])

        assert resumed is False
        assert portrait.read_text(encoding="utf-8").splitlines() == [a, b]

    def test_last_sessions_queue_comes_back_from_its_top_when_its_video_is_gone(self, tmp_path: Path):
        a, b = _videos(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "landscape_playlist.tsv"
        _write_playlist(playlist, [a, b])

        assert resume_playlists([(playlist, str(tmp_path / "deleted.mp4"))]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [a, b]

    def test_drops_videos_that_no_longer_exist(self, tmp_path: Path):
        a, b = _videos(tmp_path, "a.mp4", "b.mp4")
        gone = str(tmp_path / "gone.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, gone, b])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, a]

    def test_a_list_an_older_session_kept_aside_is_deleted_and_never_put_back(self, tmp_path: Path):
        a, image = _videos(tmp_path, "a.mp4", "image.png")
        playlist = tmp_path / "portrait_playlist.tsv"
        kept = tmp_path / "portrait_playlist.kept.tsv"
        _write_playlist(kept, [a])
        _write_playlist(playlist, [image])

        assert resume_playlists([(playlist, image)]) is True

        assert not kept.exists()
        assert playlist.read_text(encoding="utf-8").splitlines() == [image]

    def test_a_list_kept_aside_is_deleted_even_when_nothing_resumes(self, tmp_path: Path):
        kept = tmp_path / "landscape_playlist.kept.tsv"
        _write_playlist(kept, [_videos(tmp_path, "a.mp4")[0]])

        assert resume_playlists([(tmp_path / "landscape_playlist.tsv", "")]) is False

        assert not kept.exists()


class TestResumeSharedState:
    def test_an_app_saved_before_it_kept_its_funestras_settings_takes_their_last_reports(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state_file.write_text("[state]\nvolume = 40\n", encoding="utf-8")

        state = resume_shared_state(state_file, resumed=True, last_reported=FunestraReports(
            main=MainFunestraStatus(speed=1.5, read_at=1_700_000_000.0)))

        assert (state.volume, state.main_speed) == (40, 1.5)

    def test_an_app_saved_before_it_kept_the_mic_takes_the_flag_it_left_and_spends_it(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state_file.write_text("[state]\nvolume = 40\n", encoding="utf-8")
        (tmp_path / "mic_off.flag").write_text("1", encoding="utf-8")

        state = resume_shared_state(state_file, resumed=True)

        assert state.mic_muted is True
        assert not (tmp_path / "mic_off.flag").exists()

    def test_what_a_funestra_reported_after_the_app_last_wrote_its_state_comes_back(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_speed=0.5))

        state = resume_shared_state(state_file, resumed=True, last_reported=FunestraReports(
            main=MainFunestraStatus(speed=1.5, read_at=1_700_000_000.0)))

        assert state.main_speed == 1.5

    def test_every_setting_it_remembers_comes_back(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        left = replace(shifted_state(BridgeState()), osr2_control=OSR2_RETRACTED)
        write_shared_state(state_file, left)

        state = resume_shared_state(state_file, resumed=True)

        assert remembered_values(state) == remembered_values(left)

    def test_carries_each_funestras_own_f_mode_onto_the_resumed_session(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(favorites_filter=True), portrait=SatelliteState(favorites_filter=False), main_scripted_filter=True))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.main_scripted_filter, state.satellite(FunTimeFunestra.PORTRAIT).favorites_filter,
                state.satellite(FunTimeFunestra.LANDSCAPE).favorites_filter) == (True, False, True)

    def test_carries_a_running_loop_and_the_map_it_hangs_on(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(loop="action", nav_map_anchor="C:/v/b.mp4"), portrait=SatelliteState(loop="seed", nav_map_anchor="C:/v/a.mp4", widen_video="C:/v/a.mp4")))

        state = resume_shared_state(state_file, resumed=True)

        assert state.satellite(FunTimeFunestra.PORTRAIT).loop == "seed"
        assert state.satellite(FunTimeFunestra.PORTRAIT).nav_map_anchor == "C:/v/a.mp4"
        assert state.satellite(FunTimeFunestra.PORTRAIT).widen_video == "C:/v/a.mp4"
        assert state.satellite(FunTimeFunestra.LANDSCAPE).loop == "action"
        assert state.satellite(FunTimeFunestra.LANDSCAPE).nav_map_anchor == "C:/v/b.mp4"

    def test_carries_each_satellite_s_filter_and_order(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(filter="beta gamma", latest=True), portrait=SatelliteState(filter="alpha", latest=True)))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.satellite(FunTimeFunestra.PORTRAIT).filter, state.satellite(FunTimeFunestra.LANDSCAPE).filter) == ("alpha", "beta gamma")
        assert state.satellite(FunTimeFunestra.PORTRAIT).latest is True
        assert state.satellite(FunTimeFunestra.LANDSCAPE).latest is True

    def test_carries_the_main_funestra_s_order(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_latest=True))

        state = resume_shared_state(state_file, resumed=True)

        assert state.main_latest is True

    def test_carries_genau_s_order(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(genau_latest=True))

        state = resume_shared_state(state_file, resumed=True)

        assert state.genau_latest is True

    def test_carries_the_sound_level_and_each_satellite_s_lock(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(locked=False), portrait=SatelliteState(locked=True), volume=40, muted=True))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.volume, state.muted) == (40, True)
        assert (state.satellite(FunTimeFunestra.PORTRAIT).locked, state.satellite(FunTimeFunestra.LANDSCAPE).locked) == (True, False)

    def test_carries_what_the_osr2_was_left_in(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        for control in (OSR2_PARKED, OSR2_RETRACTED):
            write_shared_state(state_file, BridgeState(osr2_control=control))

            assert resume_shared_state(state_file, resumed=True).osr2_control == control

    def test_a_session_left_under_control_off_comes_back_parked(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(osr2_control="control_off"))

        assert resume_shared_state(state_file, resumed=True).osr2_control == OSR2_PARKED

    def test_carries_which_funestra_has_the_osr2(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(osr2_funestra=FunTimeFunestra.LANDSCAPE))

        assert resume_shared_state(state_file, resumed=True).osr2_funestra == FunTimeFunestra.LANDSCAPE

    def test_carries_the_mode_the_primary_slot_was_left_in(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_mode=MainMode.GENAU))

        assert resume_shared_state(state_file, resumed=True).main_mode is MainMode.GENAU

    def test_drops_the_state_nothing_on_disk_brings_back(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(nav_anchor="C:/v/b.mp4"), portrait=SatelliteState(nav_anchor="C:/v/a.mp4"), omni_paused=True, active_funestra=3))

        state = resume_shared_state(state_file, resumed=True)

        fresh = BridgeState()
        assert (state.omni_paused, state.active_funestra) == (
            fresh.omni_paused, fresh.active_funestra)
        for name in NOT_RESUMED:
            assert getattr(state, name) == getattr(fresh, name)
        for funestra in FunTimeFunestra.SATELLITES:
            for name in NOT_RESUMED_PER_SATELLITE:
                assert getattr(state.satellite(funestra), name) == getattr(
                    fresh.satellite(funestra), name)

    def test_an_app_reopens_with_the_windows_where_startup_puts_them(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(crowned=Crown.MAIN, majority=Crown.MAIN))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.crowned, state.majority) == (Crown.MAIN, Crown.PORTRAIT)

    def test_the_state_left_behind_is_named_positively_and_is_all_of_it(self):
        names = {field.name for field in fields(BridgeState)}
        assert names >= NOT_RESUMED, sorted(NOT_RESUMED - names)
        assert set(RESUMED_FIELDS) == names - NOT_RESUMED

        satellite_names = {field.name for field in fields(SatelliteState)}
        assert satellite_names >= NOT_RESUMED_PER_SATELLITE, sorted(NOT_RESUMED_PER_SATELLITE - satellite_names)
        assert set(RESUMED_SATELLITE_FIELDS) == satellite_names - NOT_RESUMED_PER_SATELLITE

    def test_satellites_built_fresh_start_over_and_every_other_setting_comes_back(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        left = replace(shifted_state(BridgeState()), osr2_control=OSR2_RETRACTED)
        write_shared_state(state_file, left)

        state = resume_shared_state(state_file, resumed=False)

        started_over = left
        for funestra in FunTimeFunestra.SATELLITES:
            started_over = started_over.with_satellite(funestra, **{
                name: getattr(SatelliteState(), name) for name in DESCRIBES_THE_PLAYLIST})
        assert remembered_values(replace(state, main_shuffle_seed=0)) == remembered_values(
            replace(started_over, main_shuffle_seed=0))

    def test_a_fresh_session_deals_the_main_funestra_a_shuffle_of_its_own(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"

        seeds = {resume_shared_state(state_file, resumed=False).main_shuffle_seed
                 for _ in range(3)}

        assert len(seeds) == 3

    def test_the_carry_is_on_disk_where_the_dispatch_loop_reads_its_state(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(portrait=SatelliteState(favorites_filter=True), omni_paused=True))

        resume_shared_state(state_file, resumed=True)

        written = read_shared_state(state_file)
        assert written is not None
        assert written.satellite(FunTimeFunestra.PORTRAIT).favorites_filter is True
        assert written.omni_paused is False

    def test_a_first_run_leaves_a_state_file(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"

        assert replace(resume_shared_state(state_file, resumed=True), main_shuffle_seed=0) == (
            BridgeState())
        assert state_file.exists()


class TestResumeSatelliteLocks:
    def test_queues_a_lock_for_the_satellite_that_was_holding_one(self, tmp_path: Path):
        portrait = tmp_path / "portrait_cmd.txt"
        landscape = tmp_path / "landscape_cmd.txt"

        resume_satellite_locks([(portrait, True), (landscape, False)])

        assert portrait.read_text(encoding="utf-8").split() == ["LOCK_ON"]
        assert not landscape.exists()

    def test_a_satellite_left_unlocked_as_one_opens_is_sent_nothing(self, tmp_path: Path):
        portrait = tmp_path / "portrait_cmd.txt"

        resume_satellite_locks([(portrait, False)])

        assert not portrait.exists()

    def test_keeps_whatever_is_already_queued(self, tmp_path: Path):
        portrait = tmp_path / "portrait_cmd.txt"
        portrait.write_text("RELOAD_PLAYLIST\n", encoding="utf-8")

        resume_satellite_locks([(portrait, True)])

        assert portrait.read_text(encoding="utf-8").split() == ["RELOAD_PLAYLIST", "LOCK_ON"]


class TestResumeMainLoop:
    def _playlist(self, tmp_path: Path, *videos: str) -> Path:
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, list(_videos(tmp_path, *videos)))
        return playlist

    def _looping(self, tmp_path: Path, video: str) -> BridgeState:
        return BridgeState(main_loop_video=str(tmp_path / video), main_loop_bounds_ms="2000 4000")

    def test_queues_the_range_the_primary_was_looping(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        playlist = self._playlist(tmp_path, "a.mp4", "b.mp4")

        resume_main_loop(main_funestra_cmd, self._looping(tmp_path, "a.mp4"), playlist)

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == ["SET_LOOP 2000 4000"]

    def test_a_main_funestra_left_playing_through_as_one_opens_is_sent_nothing(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        playlist = self._playlist(tmp_path, "a.mp4")
        on_the_video_that_leads_with_no_loop = replace(
            self._looping(tmp_path, "a.mp4"), main_loop_bounds_ms="")

        resume_main_loop(main_funestra_cmd, on_the_video_that_leads_with_no_loop, playlist)

        assert not main_funestra_cmd.exists()

    def test_queues_nothing_when_another_video_leads_the_playlist(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        playlist = self._playlist(tmp_path, "b.mp4")

        resume_main_loop(main_funestra_cmd, self._looping(tmp_path, "a.mp4"), playlist)

        assert not main_funestra_cmd.exists()

    def test_keeps_whatever_is_already_queued(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        main_funestra_cmd.write_text("SET_VOLUME 40 0\n", encoding="utf-8")
        playlist = self._playlist(tmp_path, "a.mp4")

        resume_main_loop(main_funestra_cmd, self._looping(tmp_path, "a.mp4"), playlist)

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == [
            "SET_VOLUME 40 0", "SET_LOOP 2000 4000",
        ]

class TestResumeRates:
    def test_queues_the_rate_each_funestra_was_playing_at(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        portrait_cmd = tmp_path / "portrait_cmd.txt"

        resume_rates([(main_funestra_cmd, 0.25), (portrait_cmd, 1.5)])

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == ["SET_SPEED 0.25"]
        assert portrait_cmd.read_text(encoding="utf-8").splitlines() == ["SET_SPEED 1.5"]

    def test_queues_nothing_for_a_funestra_at_normal_speed(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"

        resume_rates([(main_funestra_cmd, 1.0)])

        assert not main_funestra_cmd.exists()


class TestResumeSatelliteVolumes:
    def test_a_satellite_gets_back_the_level_and_mute_it_was_left_at(self, tmp_path: Path):
        portrait_cmd, landscape_cmd = tmp_path / "portrait_cmd.txt", tmp_path / "landscape_cmd.txt"
        carried = BridgeState().with_satellite(FunTimeFunestra.PORTRAIT, volume=40, muted=False)

        resume_what_lives_in_a_funestra(
            carried, main_funestra_cmd_file=tmp_path / "main_player_cmd.txt",
            satellite_cmd_files={FunTimeFunestra.PORTRAIT: portrait_cmd,
                                 FunTimeFunestra.LANDSCAPE: landscape_cmd},
            genau_cmd_file=tmp_path / "genau_cmd.txt")

        assert portrait_cmd.read_text(encoding="utf-8").splitlines() == ["SET_VOLUME 40 0"]
        assert not landscape_cmd.exists()


class TestResumeMainLock:
    def test_queues_the_unlock_a_main_funestra_was_left_in(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"

        resume_main_lock(main_funestra_cmd, locked=False)

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == ["LOCK_OFF"]

    def test_a_main_funestra_left_holding_its_video_as_one_opens_is_sent_nothing(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"

        resume_main_lock(main_funestra_cmd, locked=True)

        assert not main_funestra_cmd.exists()


class TestWhatARestartBringsBackOfEachFunestra:
    _KEPT_IN_THE_APPS_STATE: ClassVar[dict] = {
        "SET_SPEED": {"main_speed", "speed"}, "SPEED_UP": {"main_speed", "speed"},
        "SPEED_DOWN": {"main_speed", "speed"}, "LOCK_ON": {"main_locked", "locked"},
        "LOCK_OFF": {"main_locked", "locked"}, "TOGGLE_LOCK": {"main_locked", "locked"},
        "SET_MAX_INTENSITY": {"max_intensity"}, "SET_VOLUME": {"volume", "muted"},
        "SHOW": {"main_mode"},
        "SET_F_MODE": {"main_scripted_filter"}}
    _NO_SETTING = frozenset({
        "NEXT", "PREV", "QUIT", "RELOAD_PLAYLIST", "SEEK_BACK", "SEEK_FWD", "TRASH", "SHOW_FRAME",
        "CLEAR_FRAME", "SET_PACE", "HOLD_PICTURE", "PLAY_FILE", "PLAY_NEXT", "NEXT_VERSION",
        "PREV_VERSION", "FORGET_VERSIONS", "LET_GO", "JUMP_TO_FUNSCRIPT", "NEXT_FUNSCRIPTED"})
    _RESENT_EVERY_SECOND_BY_THE_DEVICE_ARBITER = frozenset({"SET_TCODE_ENABLED"})
    _ANSWERED_FROM_THE_NEXT_FUNESTRA_ON = frozenset({"FORGET_VERSIONS"})

    def test_every_verb_a_funestra_answers_comes_back_after_a_restart_or_is_no_setting(self):
        named = (set(self._KEPT_IN_THE_APPS_STATE) | self._NO_SETTING
                 | self._RESENT_EVERY_SECOND_BY_THE_DEVICE_ARBITER)
        verbs = {name for name, spelled in vars(funestra_verbs).items() if spelled == name}

        assert verbs - named == set(), f"say how a restart brings back {sorted(verbs - named)}"
        no_longer = named - verbs - self._ANSWERED_FROM_THE_NEXT_FUNESTRA_ON
        assert no_longer == set(), f"a Funestra no longer answers {sorted(no_longer)}"

    def test_what_a_restart_resends_is_what_the_apps_state_keeps(self, tmp_path: Path):
        main_funestra_cmd, portrait_cmd = tmp_path / "main_player_cmd.txt", tmp_path / "portrait_cmd.txt"
        carried = shifted_state(BridgeState())

        resume_what_lives_in_a_funestra(
            carried, main_funestra_cmd_file=main_funestra_cmd,
            satellite_cmd_files={FunTimeFunestra.PORTRAIT: portrait_cmd},
            genau_cmd_file=tmp_path / "genau_cmd.txt")
        resume_satellite_locks([(portrait_cmd, True)])

        assert {line.split()[0] for command_file in (main_funestra_cmd, portrait_cmd)
                for line in command_file.read_text(encoding="utf-8").splitlines()} == {
            funestra_verbs.SET_SPEED, funestra_verbs.LOCK_OFF, funestra_verbs.SET_VOLUME,
            funestra_verbs.LOCK_ON}
        assert {funestra_verbs.SET_SPEED, funestra_verbs.LOCK_OFF, funestra_verbs.SET_VOLUME,
                funestra_verbs.LOCK_ON} <= {
            getattr(funestra_verbs, name) for name in self._KEPT_IN_THE_APPS_STATE}

    def test_what_the_apps_state_keeps_for_a_funestra_is_remembered(self):
        assert set().union(*self._KEPT_IN_THE_APPS_STATE.values()) <= (
            set(RESUMED_FIELDS) | set(RESUMED_SATELLITE_FIELDS))


class TestResumeGenau:
    def test_queues_the_bars_the_hand_was_left_at(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_speed=76, genau_amplitude=60, genau_center=30,
                                            genau_flick_seconds=25))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
            "SPEED 76", "AMP 60", "CENTER 30", "FLICK_SECONDS 25",
        ]

    def test_queues_nothing_for_a_hand_left_where_the_next_one_opens(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState())

        assert not genau_cmd.exists()

    def test_says_a_shape_as_the_steps_that_reach_it(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_shape="sawtooth"))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
            "CYCLE_SHAPE", "CYCLE_SHAPE", "CYCLE_SHAPE",
        ]

    def test_queues_the_learned_motion_that_was_running(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_learned=True))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == ["LEARNED_ON"]

    def test_queues_the_unlock_genau_was_left_in(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_locked=False))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == ["LOCK_OFF"]

    _KEPT_IN_THE_APPS_STATE: ClassVar[dict] = {
        "speed": "genau_speed", "amplitude": "genau_amplitude", "center": "genau_center",
        "shape": "genau_shape", "flick_seconds": "genau_flick_seconds",
        "learned": "genau_learned", "lock": "genau_locked", "max_intensity": "max_intensity",
        "volume": "volume", "browse_order": "genau_latest", "shapes": "genau_plays_vr",
        "hold": "osr2_control"}
    _NO_SETTING = frozenset({
        "quit", "flick", "condemn", "play_file", "flip_ends", "pause", "tcode", "hud"})

    def test_every_control_genau_has_comes_back_after_a_restart_or_is_no_setting(self):
        named = set(self._KEPT_IN_THE_APPS_STATE) | self._NO_SETTING
        controls = {control.name for control, _ in GENAU_VERBS.values()}

        assert controls == named, (
            f"say how a restart brings back {sorted(controls - named)}; "
            f"Genau no longer has {sorted(named - controls)}")

    def test_a_restart_resends_what_the_apps_state_keeps_for_genau(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, replace(shifted_state(BridgeState()), genau_shape="sawtooth"))

        resent = {GENAU_VERBS[line.split()[0]][0].name
                  for line in genau_cmd.read_text(encoding="utf-8").splitlines()}
        assert {self._KEPT_IN_THE_APPS_STATE[control] for control in resent} == {
            name for name in RESUMED_FIELDS if name.startswith("genau_")
            and name not in ("genau_latest", "genau_plays_vr", "genau_plays_flat",
                             "genau_favorites_filter", "genau_plays_portrait",
                             "genau_plays_landscape")}

    def test_what_the_apps_state_keeps_for_genau_is_remembered(self):
        assert set(self._KEPT_IN_THE_APPS_STATE.values()) <= set(RESUMED_FIELDS)

    _KEPT_FOR_ITS_LAUNCH: ClassVar[dict] = {"f_mode": ("genau_favorites_filter",),
                            "orientations": ("genau_plays_portrait", "genau_plays_landscape")}

    def test_every_verb_genau_answers_on_the_main_funestras_channel_comes_back_at_its_launch(self):
        controls = {control.name for control, _ in GENAUS_VERBS_ON_THE_MAIN_FUNESTRA.values()}

        assert controls == set(self._KEPT_FOR_ITS_LAUNCH)
        assert {field for fields in self._KEPT_FOR_ITS_LAUNCH.values() for field in fields} <= set(
            RESUMED_FIELDS)


class TestOpeningTheMainList:
    def _library(self, tmp_path: Path) -> tuple[MainList, list[str], list[str]]:
        vr_dir, flat_dir = tmp_path / "library" / "VR", tmp_path / "library" / "2D"
        for folder in (vr_dir, flat_dir):
            folder.mkdir(parents=True)
        vr = _videos(vr_dir, "scene one.mp4", "scene two.mp4")
        flat = _videos(flat_dir, "scene three.mp4", "scene four.mp4")
        the_list = MainList(sources=f"{vr_dir}|{flat_dir}", state_dir=tmp_path / "state",
                            vr_dirs=str(vr_dir))
        return the_list, vr, flat

    def test_vr_only_opens_on_vr_videos_whatever_the_last_list_held(self, tmp_path: Path):
        the_list, vr, flat = self._library(tmp_path)
        _write_playlist(the_list.file, [*flat, *vr])

        open_the_main_list(the_list, BridgeState(main_plays_flat=False),
                           state_file=tmp_path / "state" / "shared_bridge_state.ini",
                           on_screen=flat[0])

        assert sorted(the_list.file.read_text(encoding="utf-8").splitlines()) == sorted(vr)

    def test_a_desktop_session_opens_on_its_own_library_whatever_the_headset_left(
        self, tmp_path: Path,
    ):
        _headset_list, vr, flat = self._library(tmp_path)
        desktop = MainList(sources=str(Path(flat[0]).parent), state_dir=tmp_path / "state")
        _write_playlist(desktop.file, [*vr, *flat])

        open_the_main_list(desktop, BridgeState(),
                           state_file=tmp_path / "state" / "shared_bridge_state.ini",
                           on_screen=vr[0])

        assert sorted(desktop.file.read_text(encoding="utf-8").splitlines()) == sorted(flat)

    def test_it_opens_on_the_video_it_was_left_on_so_its_loop_can_come_back(self, tmp_path: Path):
        the_list, vr, _flat = self._library(tmp_path)

        open_the_main_list(the_list, BridgeState(),
                           state_file=tmp_path / "state" / "shared_bridge_state.ini",
                           on_screen=vr[1])

        assert playlist_opens_on(the_list.file, vr[1]) is True

    def test_with_nothing_let_through_and_nothing_to_hold_its_buttons_go_back_to_their_defaults(
        self, tmp_path: Path,
    ):
        the_list, vr, flat = self._library(tmp_path)
        state_file = tmp_path / "state" / "shared_bridge_state.ini"
        left = BridgeState(main_plays_vr=False, main_plays_flat=False, main_latest=True,
                           main_shuffle_seed=7, volume=40)

        opened = open_the_main_list(the_list, left, state_file=state_file,
                                    on_screen=str(tmp_path / "library" / "gone since.mp4"))

        assert opened == replace(left, main_plays_vr=True, main_plays_flat=True, main_latest=False)
        assert read_shared_state(state_file) == opened
        assert sorted(the_list.file.read_text(encoding="utf-8").splitlines()) == sorted(vr + flat)


class TestPlaylistOpensOn:
    def test_the_video_a_resumed_playlist_leads_with(self, tmp_path: Path):
        a, b = _videos(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, [b, a])

        assert playlist_opens_on(playlist, b) is True
        assert playlist_opens_on(playlist, a) is False

    def test_case_alone_is_not_a_different_video(self, tmp_path: Path):
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, [str(tmp_path / "Sub" / "a.mp4")])

        assert playlist_opens_on(playlist, str(tmp_path / "sub" / "A.MP4")) is True

    def test_a_funscript_column_does_not_hide_the_video(self, tmp_path: Path):
        a, b = _videos(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, [f"{a}\ta.funscript", b])

        assert playlist_opens_on(playlist, a) is True

    def test_an_empty_playlist_opens_on_nothing(self, tmp_path: Path):
        assert playlist_opens_on(tmp_path / "absent.tsv", "C:/v/a.mp4") is False


def test_a_session_opens_with_the_browsers_place_where_it_was_left(tmp_path):
    state_file = tmp_path / "shared_state.ini"
    write_shared_state(state_file, BridgeState(rfb_mode=RfbMode.ORIGENERATOR))

    resumed = resume_shared_state(state_file, resumed=True)

    assert resumed.rfb_mode is RfbMode.ORIGENERATOR
