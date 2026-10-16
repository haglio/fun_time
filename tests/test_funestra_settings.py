from __future__ import annotations

from funestra.drive_readout import DriveHud
from funestra.modes import LoopState, SatellitesMode

from fun_time.funestra_settings import FunestraReports, with_what_the_funestras_report
from fun_time.funestra_status import GenauStatus, MainFunestraStatus
from fun_time.room_funestras import RoomFunestra
from fun_time.satellite_control import SatelliteStatus
from fun_time.shared_state import BridgeState

PUBLISHED_AT = 1_700_000_000.0


class TestTheRoomRecordsWhatEachFunestraReports:
    def test_the_rate_each_funestra_plays_at(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            main=MainFunestraStatus(speed=1.5, read_at=PUBLISHED_AT),
            satellites={RoomFunestra.PORTRAIT: SatelliteStatus(speed=0.5, read_at=PUBLISHED_AT)}))

        assert (state.main_speed, state.satellite(RoomFunestra.PORTRAIT).speed,
                state.satellite(RoomFunestra.LANDSCAPE).speed) == (1.5, 0.5, 1.0)

    def test_whether_the_main_funestra_holds_its_video_and_the_loop_it_runs(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            main=MainFunestraStatus(video="C:/v/a.mp4", locked=False, loop_state=LoopState.LOOPING,
                                    loop_in_ms=1000, loop_out_ms=5000, read_at=PUBLISHED_AT)))

        assert (state.main_locked, state.main_loop_video, state.main_loop_in_ms,
                state.main_loop_out_ms) == (False, "C:/v/a.mp4", 1000, 5000)

    def test_a_loop_let_go_of_is_forgotten(self):
        looping = BridgeState(main_loop_video="C:/v/a.mp4", main_loop_in_ms=1000,
                              main_loop_out_ms=5000)

        state = with_what_the_funestras_report(looping, FunestraReports(
            main=MainFunestraStatus(video="C:/v/a.mp4", read_at=PUBLISHED_AT)))

        assert (state.main_loop_video, state.main_loop_in_ms, state.main_loop_out_ms) == ("", 0, 0)

    def test_genaus_bars_shape_flick_seconds_and_switches(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(
            genau_drive=DriveHud(speed=76, amplitude=60, center=30, shape="sawtooth",
                                 advance_interval=25),
            genau=GenauStatus(learned_active=True, locked=False)))

        assert (state.genau_speed, state.genau_amplitude, state.genau_center, state.genau_shape,
                state.genau_flick_seconds, state.genau_learned, state.genau_locked) == (
            76, 60, 30, "sawtooth", 25, True, False)

    def test_not_the_rate_of_a_show_the_hosted_app_plays_on_a_satellite(self):
        hosting = BridgeState().with_satellite(RoomFunestra.PORTRAIT, mode=SatellitesMode.ORIGENERATOR)

        state = with_what_the_funestras_report(hosting, FunestraReports(
            satellites={RoomFunestra.PORTRAIT: SatelliteStatus(speed=0.5, read_at=PUBLISHED_AT)}))

        assert state.satellite(RoomFunestra.PORTRAIT).speed == 1.0

    def test_the_level_and_mute_each_satellites_own_chip_is_at(self):
        state = with_what_the_funestras_report(BridgeState(), FunestraReports(satellites={
            RoomFunestra.PORTRAIT: SatelliteStatus(volume=40, muted=False),
            RoomFunestra.LANDSCAPE: SatelliteStatus()}))

        assert (state.satellite(RoomFunestra.PORTRAIT).volume,
                state.satellite(RoomFunestra.PORTRAIT).muted) == (40, False)
        assert (state.satellite(RoomFunestra.LANDSCAPE).volume,
                state.satellite(RoomFunestra.LANDSCAPE).muted) == (100, True)
