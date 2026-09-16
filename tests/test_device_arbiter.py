"""Who has the OSR2 in video mode, moment to moment.

The Robot Hand and a funscript both feed the broker's one UDP T-Code inlet, so exactly
one may drive.  The arbiter's whole world is the main player's status file and the two
command files, so these drive it with nothing else around it.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from player_core.console import OSR2_CONTROL_OFF, OSR2_DRIVING
from player_core.funscript import PARK_TOUCH_WAIT_CAP_MS

from fun_time.device_arbiter import REASSERT_S, DeviceArbiter
from fun_time.robot_hand_hold import STILL_COMMANDS
from tests.role_window_fakes import FakeClock


def make_driver(tmp_path: Path, clock=None) -> DeviceArbiter:
    return DeviceArbiter(
        main_player_status_file=tmp_path / "main_player_status.txt",
        main_player_cmd_file=tmp_path / "main_player_cmd.txt",
        genau_cmd_file=tmp_path / "rh_cmd.txt",
        clock=clock or FakeClock(),
    )


def publish_main_player(driver: DeviceArbiter, *, has_funscript=True, resting=False,
                position_ms=10, touch_ms=None) -> None:
    touch = "" if touch_ms is None else str(touch_ms)
    driver.main_player_status_file.write_text(
        "video=C:\\clip.mp4\n"
        f"position_ms={position_ms}\n"
        f"has_funscript={1 if has_funscript else 0}\n"
        f"funscript_resting={1 if resting else 0}\n"
        f"handoff_touch_ms={touch}\n",
        encoding="utf-8",
    )


def genau(driver: DeviceArbiter) -> str:
    # The arbiter appends its verbs (a shared single slot clobbered a handoff
    # once), so reads strip the trailing newline.
    return driver.genau_cmd_file.read_text(encoding="utf-8").strip()


def main_player(driver: DeviceArbiter) -> str:
    return driver.main_player_cmd_file.read_text(encoding="utf-8").strip()


class TestControlOff:
    """The console's fourth control state: nothing here moves the device, in
    either mode.

    The point of doing it here rather than at the button is that this is the
    thing that re-states its pair every second -- a verb fired from a handler
    would be undone by the arbiter's very next assertion.
    """

    def test_the_funscript_is_gated_and_the_motion_flattened_in_either_mode(
            self, tmp_path):
        for mode in ("video", "genau"):
            driver = make_driver(tmp_path / mode)
            driver.main_player_cmd_file.parent.mkdir(parents=True, exist_ok=True)
            publish_main_player(driver)

            driver.sync(mode, paused=False, control=OSR2_CONTROL_OFF)

            assert main_player(driver) == "SET_TCODE_ENABLED 0", mode
            assert genau(driver).splitlines() == list(STILL_COMMANDS), mode

    def test_the_motion_is_stilled_rather_than_paused(self, tmp_path):
        """PAUSE stops Genau's clips too, and letting go of the OSR2 must not
        stop the picture on the main slot."""
        driver = make_driver(tmp_path)
        publish_main_player(driver)

        driver.sync("genau", paused=False, control=OSR2_CONTROL_OFF)

        assert "PAUSE" not in genau(driver)

    def test_the_device_is_not_moved_by_letting_go_of_it(self, tmp_path):
        """park and retract are the two presses that MOVE the device; this is
        the one that leaves it exactly where it stands."""
        driver = make_driver(tmp_path)
        publish_main_player(driver)

        driver.sync("video", paused=False, control=OSR2_CONTROL_OFF)

        assert "CENTER" not in genau(driver)

    def test_the_stilling_is_re_stated_on_the_heartbeat_and_not_every_tick(
            self, tmp_path):
        clock = FakeClock()
        driver = make_driver(tmp_path, clock=clock)
        publish_main_player(driver)

        driver.sync("video", paused=False, control=OSR2_CONTROL_OFF)
        driver.sync("video", paused=False, control=OSR2_CONTROL_OFF)
        assert genau(driver).splitlines() == list(STILL_COMMANDS)

        clock.advance(REASSERT_S)
        driver.sync("video", paused=False, control=OSR2_CONTROL_OFF)
        assert genau(driver).splitlines() == list(STILL_COMMANDS) * 2

    def test_control_coming_back_stops_the_stilling(self, tmp_path):
        """What puts the motion back is the press itself -- the driving button
        replays what it wrote down -- so all this has to do is stop asserting."""
        clock = FakeClock()
        driver = make_driver(tmp_path, clock=clock)
        publish_main_player(driver)
        driver.sync("video", paused=False, control=OSR2_CONTROL_OFF)

        clock.advance(REASSERT_S)
        driver.sync("genau", paused=False, control=OSR2_DRIVING)
        driver.sync("genau", paused=False, control=OSR2_DRIVING)

        assert genau(driver).splitlines() == list(STILL_COMMANDS)

    def test_the_handoff_re_asserts_when_control_comes_back(self, tmp_path):
        """Nobody had the device through the silence, so re-entry must say who
        has it again rather than believing the driver it remembered."""
        clock = FakeClock()
        driver = make_driver(tmp_path, clock=clock)
        publish_main_player(driver)
        driver.sync("video", paused=False, control=OSR2_DRIVING)
        clock.advance(REASSERT_S)
        driver.sync("video", paused=False, control=OSR2_CONTROL_OFF)

        clock.advance(REASSERT_S)
        driver.sync("video", paused=False, control=OSR2_DRIVING)

        assert genau(driver).splitlines()[-1] == "PAUSE"
        assert main_player(driver).splitlines()[-1] == "SET_TCODE_ENABLED 1"


class TestVideoModeFunscriptHandoff:
    """The funscript drives its scripted stretches (the hand yields), and the
    Robot Hand drives the unscripted ones — a funscript's quiet lead-in and its interior
    gaps, which the main player flags as ``funscript_resting``."""

    def test_the_flip_waits_for_the_touch_the_trace_chose(self, tmp_path):
        """The main player publishes the touch-down its picture drew the blue ending on;
        The hand keeps the device until the playhead reaches it.  When each side
        chose its own touch from its own read of the wave, the arbiter could
        take an earlier one — and the leftover drawn blue vanished the moment
        the dot reached it."""
        driver = make_driver(tmp_path)
        publish_main_player(driver, resting=True, position_ms=14_000)
        driver.sync("video", paused=False)           # the hand's turn first
        publish_main_player(driver, resting=False, position_ms=15_100, touch_ms=16_400)

        driver.sync("video", paused=False)

        assert genau(driver) == "RESUME"              # still the hand's

    def test_the_held_flip_lands_when_the_playhead_reaches_the_touch(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, resting=True, position_ms=14_000)
        driver.sync("video", paused=False)
        publish_main_player(driver, resting=False, position_ms=15_100, touch_ms=16_400)
        driver.sync("video", paused=False)

        publish_main_player(driver, resting=False, position_ms=16_450, touch_ms=16_400)
        driver.sync("video", paused=False)

        assert genau(driver).splitlines()[-1] == "PAUSE"

    def test_no_published_touch_flips_at_once(self, tmp_path):
        """The ramp case — a raised floor — has no touch to wait for: the
        descent is the drawn ramp, walked by the main player's driver."""
        driver = make_driver(tmp_path)
        publish_main_player(driver, resting=True, position_ms=14_000)
        driver.sync("video", paused=False)
        publish_main_player(driver, resting=False, position_ms=15_100)

        driver.sync("video", paused=False)

        assert genau(driver).splitlines()[-1] == "PAUSE"

    def test_a_stalled_playhead_cannot_hold_the_flip_forever(self, tmp_path):
        clock = FakeClock()
        driver = make_driver(tmp_path, clock)
        publish_main_player(driver, resting=True, position_ms=14_000)
        driver.sync("video", paused=False)
        publish_main_player(driver, resting=False, position_ms=15_100, touch_ms=16_400)
        driver.sync("video", paused=False)

        clock.advance(PARK_TOUCH_WAIT_CAP_MS / 1000)        # the cap expiring
        driver.sync("video", paused=False)

        assert genau(driver).splitlines()[-1] == "PAUSE"

    def test_a_seek_into_the_script_s_turn_flips_at_once(self, tmp_path):
        """A hold honors a drawn blue ending, and a seek-entry never drew one:
        jumped into dense action from a rest, the picture already shows the
        script's turn running — even a touch left published from before the
        seek must not hold the flip."""
        driver = make_driver(tmp_path)
        publish_main_player(driver, resting=True, position_ms=1_000)
        driver.sync("video", paused=False)
        publish_main_player(driver, resting=False, position_ms=41_000,
                    touch_ms=42_000)                        # a 40s jump

        driver.sync("video", paused=False)

        assert genau(driver).splitlines()[-1] == "PAUSE"

    def test_scripted_stretch_drives_from_the_funscript(self, tmp_path):
        """Each handoff sets BOTH levers, so the two can never both be driving
        the one inlet."""
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)

        driver.sync("video", paused=False)

        assert genau(driver) == "PAUSE"               # the hand yields
        assert main_player(driver) == "SET_TCODE_ENABLED 1"   # funscript drives

    def test_funscript_gap_hands_the_stretch_to_the_robot_hand(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=True)

        driver.sync("video", paused=False)

        assert genau(driver) == "RESUME"              # the hand fills the gap
        assert main_player(driver) == "SET_TCODE_ENABLED 0"   # funscript muted

    def test_unscripted_video_drives_from_the_robot_hand(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=False)

        driver.sync("video", paused=False)

        assert genau(driver) == "RESUME"
        assert main_player(driver) == "SET_TCODE_ENABLED 0"

    def test_commands_written_only_on_change(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)
        driver.sync("video", paused=False)
        driver.genau_cmd_file.unlink()
        driver.main_player_cmd_file.unlink()

        driver.sync("video", paused=False)  # unchanged driver -> no re-issue (edge-only)

        assert not driver.genau_cmd_file.exists()
        assert not driver.main_player_cmd_file.exists()

    def test_a_lost_verb_is_retried_because_the_edge_was_never_recorded(self, tmp_path):
        """A verb queued on a file channel can still die — a writer replacing
        the file whole, a drain racing the append, a locked file exhausting
        its retries — and an arbiter that assumed delivery leaves the session
        split-brained for a whole cluster: the hand paused, the funscript never
        enabled, everything idle and grey.  So the edge is recorded only once
        BOTH verbs actually queued, and a failed one is retried next tick."""
        from player_core.file_channel import append_command as real_append

        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)

        def genau_channel_down(path, line, **kwargs):
            if path == driver.genau_cmd_file:
                return False
            return real_append(path, line, **kwargs)

        with patch("fun_time.device_arbiter.append_command",
                   side_effect=genau_channel_down):
            driver.sync("video", paused=False)

        driver.sync("video", paused=False)  # the channel healthy again: the retry

        assert genau(driver).splitlines()[-1] == "PAUSE"
        assert main_player(driver).splitlines()[-1] == "SET_TCODE_ENABLED 1"

    def test_the_standing_pair_is_requeued_on_the_slow_heartbeat(self, tmp_path):
        """Both verbs are idempotent at their players, so the standing pair
        goes out again about once a second — a verb lost AFTER the edge was
        recorded (a writer replacing the file whole) converges within that
        second instead of at the next handoff."""
        clock = FakeClock()
        driver = make_driver(tmp_path, clock)
        publish_main_player(driver, has_funscript=True, resting=False)
        driver.sync("video", paused=False)
        driver.genau_cmd_file.unlink()               # the lost verbs
        driver.main_player_cmd_file.unlink()

        clock.advance(REASSERT_S)                           # a second passes
        driver.sync("video", paused=False)

        assert genau(driver) == "PAUSE"
        assert main_player(driver) == "SET_TCODE_ENABLED 1"

    def test_entering_a_gap_flips_the_driver(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)
        driver.sync("video", paused=False)

        publish_main_player(driver, has_funscript=True, resting=True)  # gap begins
        driver.sync("video", paused=False)

        # Appended after the first flip's PAUSE; the players drain in order.
        assert genau(driver).splitlines()[-1] == "RESUME"
        assert main_player(driver).splitlines()[-1] == "SET_TCODE_ENABLED 0"

    def test_no_arbitration_outside_video_mode(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)

        driver.sync("genau", paused=False)

        assert not driver.genau_cmd_file.exists()
        assert not driver.main_player_cmd_file.exists()

    def test_no_arbitration_when_omnipaused(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)

        driver.sync("video", paused=True)

        assert not driver.genau_cmd_file.exists()
        assert not driver.main_player_cmd_file.exists()

    def test_leaving_video_mode_resets_so_reentry_reapplies(self, tmp_path):
        driver = make_driver(tmp_path)
        publish_main_player(driver, has_funscript=True, resting=False)
        driver.sync("video", paused=False)  # funscript driving

        driver.sync("genau", paused=False)   # leaves video mode -> forgets
        driver.genau_cmd_file.unlink()

        driver.sync("video", paused=False)

        assert genau(driver) == "PAUSE"
