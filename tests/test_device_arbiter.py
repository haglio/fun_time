"""Who has the OSR2, moment to moment.

The arbiter's whole world is the Funestras' status files and the command files,
so these drive it with nothing else around it.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from funestra.console import (
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
)
from funestra.file_channel import append_command as real_append
from funestra.funscript import PARK_TOUCH_WAIT_CAP_MS

from fun_time.device_arbiter import TCODE_OFF, TCODE_ON, DeviceArbiter, SatelliteLine
from fun_time.room_funestras import RoomFunestra
from tests.role_window_fakes import FakeClock


def make_driver(tmp_path: Path, clock=None, device_on=lambda: True) -> DeviceArbiter:
    return DeviceArbiter(
        main_funestra_status_file=tmp_path / "main_player_status.txt",
        main_funestra_cmd_file=tmp_path / "main_player_cmd.txt",
        genau_cmd_file=tmp_path / "rh_cmd.txt",
        satellites={funestra: SatelliteLine(status_file=tmp_path / f"{funestra.label}_status.txt",
                                          cmd_file=tmp_path / f"{funestra.label}_cmd.txt")
                    for funestra in RoomFunestra.SATELLITES},
        device_on=device_on,
        clock=clock or FakeClock(),
    )


def publish_side(tmp_path: Path, funestra: RoomFunestra, *, has_funscript=True, resting=False,
                 position_ms=10, touch_ms=None) -> None:
    (tmp_path / f"{funestra.label}_status.txt").write_text(
        f"video=C:\\clip.mp4\nposition_ms={position_ms}\n"
        f"has_funscript={1 if has_funscript else 0}\n"
        f"funscript_resting={1 if resting else 0}\n"
        f"handoff_touch_ms={'' if touch_ms is None else touch_ms}\n",
        encoding="utf-8",
    )


def publish_main_funestra(driver: DeviceArbiter, *, has_funscript=True, resting=False,
                          position_ms=10, touch_ms=None) -> None:
    touch = "" if touch_ms is None else str(touch_ms)
    driver.main_funestra_status_file.parent.mkdir(parents=True, exist_ok=True)
    driver.main_funestra_status_file.write_text(
        "video=C:\\clip.mp4\n"
        f"position_ms={position_ms}\n"
        f"has_funscript={1 if has_funscript else 0}\n"
        f"funscript_resting={1 if resting else 0}\n"
        f"handoff_touch_ms={touch}\n",
        encoding="utf-8",
    )


def told(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def side(tmp_path: Path, funestra: RoomFunestra) -> list[str]:
    return told(tmp_path / f"{funestra.label}_cmd.txt")


def genau(driver: DeviceArbiter) -> list[str]:
    return told(driver.genau_cmd_file)


def main_funestra(driver: DeviceArbiter) -> list[str]:
    return told(driver.main_funestra_cmd_file)


def the_hand_is_playing(driver: DeviceArbiter) -> bool:
    return [verb for verb in genau(driver) if verb in ("PAUSE", "RESUME")][-1] == "RESUME"


AN_HOUR_S = 3600

HAND_DRIVES = [TCODE_ON, "RESUME"]
HAND_YIELDS = ["PAUSE", TCODE_ON]
PARKED = [TCODE_OFF, "RESUME", "PARK"]
OMNIPAUSED = ["PAUSE", TCODE_ON, "PARK"]


class TestNobodyDriving:
    """Parked, the retract a relief pause leaves, and OmniPause: carried out here
    rather than at the button, because the turn is stated here whole -- a verb
    fired from a handler would be undone by the next statement."""

    def test_a_hold_gates_the_funscript_and_switches_genaus_output_off(self, tmp_path):
        """The broker walks the device to that end; Genau plays on with its output
        off, so the console can still draw the motion the hand would be making."""
        for mode in ("kino", "genau"):
            driver = make_driver(tmp_path / f"park-{mode}")
            publish_main_funestra(driver)

            driver.sync(mode, paused=False, control=OSR2_PARKED)

            assert main_funestra(driver) == [TCODE_OFF], mode
            assert genau(driver) == PARKED, mode

    def test_each_hold_tells_genau_which_end_it_holds_the_device_at(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver)

        driver.sync("kino", paused=False, control=OSR2_RETRACTED)

        assert genau(driver) == [TCODE_OFF, "RESUME", "RETRACT"]

    def test_a_hold_under_omnipause_leaves_genau_paused(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver)

        driver.sync("genau", paused=True, control=OSR2_RETRACTED)

        assert genau(driver) == [TCODE_OFF, "PAUSE", "RETRACT"]

    def test_the_room_coming_back_under_a_hold_plays_the_hand_on_unheard_at_once(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver)
        driver.sync("kino", paused=True, control=OSR2_RETRACTED)

        driver.sync("kino", paused=False, control=OSR2_RETRACTED)

        assert genau(driver) == [TCODE_OFF, "PAUSE", "RETRACT", TCODE_OFF, "RESUME", "RETRACT"]

    def test_a_switched_off_device_keeps_genaus_flick_out_of_every_hold(self, tmp_path):
        for control in (OSR2_PARKED, OSR2_RETRACTED):
            driver = make_driver(tmp_path / control, device_on=lambda: False)
            publish_main_funestra(driver)

            driver.sync("genau", paused=False, control=control)

            assert genau(driver) == [TCODE_OFF, "RESUME"], control

    def test_a_device_switched_on_under_a_hold_takes_genaus_flick_there_at_once(self, tmp_path):
        device = {"on": False}
        driver = make_driver(tmp_path, device_on=lambda: device["on"])
        publish_main_funestra(driver)
        driver.sync("genau", paused=False, control=OSR2_PARKED)

        device["on"] = True
        driver.sync("genau", paused=False, control=OSR2_PARKED)

        assert genau(driver) == [TCODE_OFF, "RESUME", *PARKED]

    def test_a_hold_is_stated_once_however_long_it_lasts(self, tmp_path):
        clock = FakeClock()
        driver = make_driver(tmp_path, clock=clock)
        publish_main_funestra(driver)

        driver.sync("kino", paused=False, control=OSR2_PARKED)
        clock.advance(AN_HOUR_S)
        driver.sync("kino", paused=False, control=OSR2_PARKED)

        assert genau(driver) == PARKED

    def test_control_coming_back_in_genau_mode_gives_the_hand_the_device_heard_again(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver)
        driver.sync("kino", paused=False, control=OSR2_PARKED)

        driver.sync("genau", paused=False, control=OSR2_DRIVING)
        driver.sync("genau", paused=False, control=OSR2_DRIVING)

        assert genau(driver) == [*PARKED, *HAND_DRIVES]

    def test_control_coming_back_hands_the_device_to_whoever_should_have_it(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver)
        driver.sync("kino", paused=False, control=OSR2_DRIVING)
        driver.sync("kino", paused=False, control=OSR2_PARKED)

        driver.sync("kino", paused=False, control=OSR2_DRIVING)

        assert genau(driver)[-2:] == HAND_YIELDS
        assert main_funestra(driver)[-1] == TCODE_ON

    def test_omnipause_takes_the_device_from_the_script_driving_it(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver)
        driver.sync("kino", paused=False)

        driver.sync("kino", paused=True)

        assert main_funestra(driver) == [TCODE_ON, TCODE_OFF]
        assert genau(driver) == [*HAND_YIELDS, *OMNIPAUSED]

    def test_omnipause_with_the_device_switched_off_leaves_genaus_flick_where_it_is(
            self, tmp_path):
        driver = make_driver(tmp_path, device_on=lambda: False)
        publish_main_funestra(driver)

        driver.sync("genau", paused=True)

        assert genau(driver) == ["PAUSE", TCODE_ON]

    def test_leaving_omnipause_in_genau_mode_gives_the_hand_the_device_back(self, tmp_path):
        driver = make_driver(tmp_path)
        driver.sync("genau", paused=True)

        driver.sync("genau", paused=False)

        assert genau(driver)[-2:] == HAND_DRIVES

    def test_leaving_omnipause_under_a_side_funestras_script_leaves_the_hand_paused(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=True, holder=RoomFunestra.PORTRAIT)

        driver.sync("genau", paused=False, holder=RoomFunestra.PORTRAIT)

        assert genau(driver)[-2:] == HAND_YIELDS
        assert side(tmp_path, RoomFunestra.PORTRAIT) == [TCODE_ON]


class TestKinoModeFunscriptHandoff:
    """The funscript drives its scripted stretches (the hand yields), and the
    Robot Hand drives the unscripted ones — a funscript's quiet lead-in and its interior
    gaps, which the Main Funestra flags as ``funscript_resting``."""

    def test_the_flip_waits_for_the_touch_the_trace_chose(self, tmp_path):
        """The Main Funestra publishes the touch-down its trace drew the blue ending on;
        the hand keeps the device until the playhead reaches it.  When each side
        chose its own touch from its own read of the wave, the arbiter could
        take an earlier one — and the leftover drawn blue vanished the moment
        the dot reached it."""
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, resting=True, position_ms=14_000)
        driver.sync("kino", paused=False)
        publish_main_funestra(driver, resting=False, position_ms=15_100, touch_ms=16_400)

        driver.sync("kino", paused=False)

        assert genau(driver) == HAND_DRIVES

    def test_the_held_flip_lands_when_the_playhead_reaches_the_touch(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, resting=True, position_ms=14_000)
        driver.sync("kino", paused=False)
        publish_main_funestra(driver, resting=False, position_ms=15_100, touch_ms=16_400)
        driver.sync("kino", paused=False)

        publish_main_funestra(driver, resting=False, position_ms=16_450, touch_ms=16_400)
        driver.sync("kino", paused=False)

        assert genau(driver)[-2:] == HAND_YIELDS

    def test_no_published_touch_flips_at_once(self, tmp_path):
        """The ramp case — a raised floor — has no touch to wait for: the
        descent is the drawn ramp, walked by the Main Funestra's driver."""
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, resting=True, position_ms=14_000)
        driver.sync("kino", paused=False)
        publish_main_funestra(driver, resting=False, position_ms=15_100)

        driver.sync("kino", paused=False)

        assert genau(driver)[-2:] == HAND_YIELDS

    def test_a_stalled_playhead_cannot_hold_the_flip_forever(self, tmp_path):
        clock = FakeClock()
        driver = make_driver(tmp_path, clock)
        publish_main_funestra(driver, resting=True, position_ms=14_000)
        driver.sync("kino", paused=False)
        publish_main_funestra(driver, resting=False, position_ms=15_100, touch_ms=16_400)
        driver.sync("kino", paused=False)

        clock.advance(PARK_TOUCH_WAIT_CAP_MS / 1000)
        driver.sync("kino", paused=False)

        assert genau(driver)[-2:] == HAND_YIELDS

    def test_a_seek_into_the_script_s_turn_flips_at_once(self, tmp_path):
        """A hold honors a drawn blue ending, and a seek-entry never drew one:
        jumped into dense action from a rest, the trace already shows the
        script's turn running — even a touch left published from before the
        seek must not hold the flip."""
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, resting=True, position_ms=1_000)
        driver.sync("kino", paused=False)
        publish_main_funestra(driver, resting=False, position_ms=41_000, touch_ms=42_000)

        driver.sync("kino", paused=False)

        assert genau(driver)[-2:] == HAND_YIELDS

    def test_scripted_stretch_drives_from_the_funscript(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)

        driver.sync("kino", paused=False)

        assert genau(driver) == HAND_YIELDS
        assert main_funestra(driver) == [TCODE_ON]

    def test_funscript_gap_hands_the_stretch_to_the_robot_hand(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=True)

        driver.sync("kino", paused=False)

        assert genau(driver) == HAND_DRIVES
        assert main_funestra(driver) == [TCODE_OFF]

    def test_unscripted_video_drives_from_the_robot_hand(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=False)

        driver.sync("kino", paused=False)

        assert genau(driver) == HAND_DRIVES
        assert main_funestra(driver) == [TCODE_OFF]

    def test_commands_written_only_on_change(self, tmp_path):
        clock = FakeClock()
        driver = make_driver(tmp_path, clock)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        driver.sync("kino", paused=False)
        driver.genau_cmd_file.unlink()
        driver.main_funestra_cmd_file.unlink()

        clock.advance(AN_HOUR_S)
        driver.sync("kino", paused=False)

        assert not driver.genau_cmd_file.exists()
        assert not driver.main_funestra_cmd_file.exists()

    def test_a_verb_that_could_not_be_queued_is_stated_again_on_the_next_tick(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)

        def genau_channel_down(path, line, **kwargs):
            if path == driver.genau_cmd_file:
                return False
            return real_append(path, line, **kwargs)

        with patch("fun_time.device_arbiter.append_command", side_effect=genau_channel_down):
            driver.sync("kino", paused=False)

        driver.sync("kino", paused=False)

        assert genau(driver) == HAND_YIELDS
        assert main_funestra(driver) == [TCODE_ON]

    def test_a_script_is_never_switched_on_while_the_hand_has_not_been_told_to_stop(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)

        def genau_channel_down(path, line, **kwargs):
            if path == driver.genau_cmd_file:
                return False
            return real_append(path, line, **kwargs)

        with patch("fun_time.device_arbiter.append_command", side_effect=genau_channel_down):
            driver.sync("kino", paused=False)

        assert main_funestra(driver) == []

    def test_entering_a_gap_flips_the_driver(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        driver.sync("kino", paused=False)

        publish_main_funestra(driver, has_funscript=True, resting=True)
        driver.sync("kino", paused=False)

        assert genau(driver)[-2:] == HAND_DRIVES
        assert main_funestra(driver)[-1] == TCODE_OFF

    def test_in_genau_mode_the_hand_drives_and_the_main_funestras_script_is_off(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)

        driver.sync("genau", paused=False)

        assert genau(driver) == HAND_DRIVES
        assert main_funestra(driver) == [TCODE_OFF]

    def test_switching_to_genau_mode_under_the_main_funestras_script_takes_it_off_the_osr2(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        driver.sync("kino", paused=False)

        driver.sync("genau", paused=False)

        assert main_funestra(driver)[-1] == TCODE_OFF
        assert genau(driver)[-2:] == HAND_DRIVES

    def test_coming_back_to_kino_mode_hands_the_device_back_to_the_script(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        driver.sync("kino", paused=False)
        driver.sync("genau", paused=False)
        driver.genau_cmd_file.unlink()

        driver.sync("kino", paused=False)

        assert genau(driver) == HAND_YIELDS


class TestASideFunestraThatHasTheOsr2:
    def test_drives_it_from_its_own_clips_funscript(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)

        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        assert side(tmp_path, RoomFunestra.PORTRAIT) == [TCODE_ON]
        assert genau(driver) == HAND_YIELDS
        assert main_funestra(driver) == [TCODE_OFF]

    def test_leaves_the_stretches_its_clip_does_not_script_to_the_robot_hand(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        publish_side(tmp_path, RoomFunestra.PORTRAIT, resting=True)

        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        assert side(tmp_path, RoomFunestra.PORTRAIT) == [TCODE_OFF]
        assert genau(driver) == HAND_DRIVES
        assert main_funestra(driver) == [TCODE_OFF]

    def test_drives_it_in_genau_mode_as_well(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.LANDSCAPE)

        driver.sync("genau", paused=False, holder=RoomFunestra.LANDSCAPE)

        assert side(tmp_path, RoomFunestra.LANDSCAPE) == [TCODE_ON]
        assert genau(driver) == HAND_YIELDS

    def test_a_side_funestra_that_never_had_it_is_told_nothing(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_funestra(driver, has_funscript=True, resting=False)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)

        driver.sync("kino", paused=False)
        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        assert side(tmp_path, RoomFunestra.LANDSCAPE) == []

    def test_the_funestra_it_took_the_osr2_from_stops_sending(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        publish_side(tmp_path, RoomFunestra.LANDSCAPE)
        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        driver.sync("kino", paused=False, holder=RoomFunestra.LANDSCAPE)

        assert side(tmp_path, RoomFunestra.PORTRAIT)[-1] == TCODE_OFF
        assert side(tmp_path, RoomFunestra.LANDSCAPE)[-1] == TCODE_ON

    def test_the_main_funestra_taking_it_back_in_genau_mode_quiets_the_side_funestra(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=False, holder=RoomFunestra.PORTRAIT)

        driver.sync("genau", paused=False, holder=RoomFunestra.MAIN)

        assert side(tmp_path, RoomFunestra.PORTRAIT)[-1] == TCODE_OFF

    def test_the_main_funestra_taking_it_back_in_genau_mode_starts_the_robot_hand_again(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=False, holder=RoomFunestra.PORTRAIT)

        driver.sync("genau", paused=False, holder=RoomFunestra.MAIN)

        assert genau(driver)[-2:] == HAND_DRIVES

    def test_a_side_funestra_let_go_of_under_omnipause_is_off_the_osr2_once_the_room_comes_back(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=False, holder=RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=True, holder=RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=True, holder=RoomFunestra.MAIN)

        driver.sync("genau", paused=False, holder=RoomFunestra.MAIN)

        assert side(tmp_path, RoomFunestra.PORTRAIT)[-1] == TCODE_OFF
        assert genau(driver)[-2:] == HAND_DRIVES

    def test_a_side_funestra_let_go_of_while_the_room_is_paused_leaves_the_robot_hand_paused(
            self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        driver.sync("genau", paused=False, holder=RoomFunestra.PORTRAIT)

        driver.sync("genau", paused=True, holder=RoomFunestra.MAIN)

        assert not the_hand_is_playing(driver)
        assert side(tmp_path, RoomFunestra.PORTRAIT)[-1] == TCODE_OFF

    def test_a_hold_switches_off_every_funestra_that_has_had_it(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_side(tmp_path, RoomFunestra.PORTRAIT)
        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        driver.sync("kino", paused=False, control=OSR2_PARKED, holder=RoomFunestra.PORTRAIT)

        assert side(tmp_path, RoomFunestra.PORTRAIT) == [TCODE_ON, TCODE_OFF]
        assert side(tmp_path, RoomFunestra.LANDSCAPE) == []
        assert main_funestra(driver)[-1] == TCODE_OFF

    def test_the_flip_to_its_script_waits_for_the_touch_its_trace_chose(self, tmp_path):
        clock = FakeClock()
        driver = make_driver(tmp_path, clock=clock)
        publish_side(tmp_path, RoomFunestra.PORTRAIT, resting=True, position_ms=1_000)
        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        publish_side(tmp_path, RoomFunestra.PORTRAIT, resting=False, position_ms=1_200, touch_ms=1_600)
        driver.sync("kino", paused=False, holder=RoomFunestra.PORTRAIT)

        assert side(tmp_path, RoomFunestra.PORTRAIT) == [TCODE_OFF]
