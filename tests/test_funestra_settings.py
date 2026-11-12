from __future__ import annotations

from funestra.osr2_visualizer import DriveHud

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_settings import FunestraReports, with_what_the_funestras_report
from fun_time.funestra_status import GenauStatus, MainFunestraStatus
from fun_time.satellite_control import SatelliteStatus
from fun_time.shared_state import BridgeState
from main_funestra.snippet_controller import SnippetState

PUBLISHED_AT = 1_700_000_000.0


class TestTheRoomRecordsWhatEachFunestraReports:
    def test_the_rate_the_main_funestra_plays_at(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            main=MainFunestraStatus(speed=1.5, read_at=PUBLISHED_AT)))

        assert state.main_speed == 1.5

    def test_whether_the_main_funestra_holds_its_video_and_the_snippet_it_runs(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            main=MainFunestraStatus(video="C:/v/a.mp4", locked=False, snippet_state=SnippetState.REPEATING,
                                    snippet_in_ms=1000, snippet_out_ms=5000, read_at=PUBLISHED_AT)))

        assert (state.main_locked, state.main_snippet_video,
                state.main_snippet_bounds_ms) == (False, "C:/v/a.mp4", "1000 5000")

    def test_a_snippet_let_go_of_is_forgotten(self):
        with_a_snippet = BridgeState(main_snippet_video="C:/v/a.mp4", main_snippet_bounds_ms="1000 5000")

        state = with_what_the_funestras_report(with_a_snippet, FunestraReports(
            main=MainFunestraStatus(video="C:/v/a.mp4", read_at=PUBLISHED_AT)))

        assert (state.main_snippet_video, state.main_snippet_bounds_ms) == ("", "")

    def test_genaus_bars_shape_flick_seconds_and_switches(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            genau_drive=DriveHud(speed=76, amplitude=60, center=30, shape="sawtooth",
                                 advance_interval=25),
            genau=GenauStatus(learned_active=True, locked=False)))

        assert (state.genau_speed, state.genau_amplitude, state.genau_center, state.genau_shape,
                state.genau_flick_seconds, state.genau_learned, state.genau_locked) == (
            76, 60, 30, "sawtooth", 25, True, False)

    def test_not_the_rate_of_the_show_the_hosted_app_plays_on_a_satellite(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            satellites={FunTimeFunestra.PORTRAIT: SatelliteStatus(speed=0.5, read_at=PUBLISHED_AT)}))

        assert state.satellite(FunTimeFunestra.PORTRAIT) == BridgeState().portrait

    def test_the_level_and_mute_each_satellites_own_chip_is_at(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(satellites={
            FunTimeFunestra.PORTRAIT: SatelliteStatus(volume=40, muted=False),
            FunTimeFunestra.LANDSCAPE: SatelliteStatus()}))

        assert (state.satellite(FunTimeFunestra.PORTRAIT).volume,
                state.satellite(FunTimeFunestra.PORTRAIT).muted) == (40, False)
        assert (state.satellite(FunTimeFunestra.LANDSCAPE).volume,
                state.satellite(FunTimeFunestra.LANDSCAPE).muted) == (100, True)
