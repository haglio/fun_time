"""The spoken holds on Genau's stroke, and the way back off one."""
from __future__ import annotations

from player_core.robot_hand import (
    POSITION_MAX,
    RobotHandState,
    phase_to_position,
    set_amplitude,
    set_center,
    set_speed,
)

from fun_time.robot_hand_hold import (
    HOLD_CENTERS,
    StrokeDials,
    dials_text,
    hold_commands,
    parse_dials,
    release_commands,
)


class TestHold:
    def test_cruise_goes_first_and_the_travel_closes_before_the_center_moves(self):
        """Cruise rewrites all three dials every tick, so numbers set under it are
        overwritten within the frame; and closing the travel first means the
        stroke stills where it is and travels to the end from there rather than
        stroking its way across."""
        assert hold_commands(0) == ("CRUISE_OFF", "AMP 0", "CENTER 0", "SPEED 0")
        assert hold_commands(100) == ("CRUISE_OFF", "AMP 0", "CENTER 100", "SPEED 0")

    def test_neither_hold_pauses_genau(self):
        """With no travel the device is already still, and an engine left playing
        answers "amp up" again — a paused one has no spoken way back out."""
        for center in HOLD_CENTERS.values():
            assert "PAUSE" not in hold_commands(center)

    def test_the_two_holds_land_the_device_on_the_ends_they_name(self):
        """The verbs are only as right as where they leave the device, so check
        the position they actually produce rather than the words that ask for it
        — park's first draft said center 100, which is the far end, not home.

        These are the two positions the broker's own PARK and RETRACT hold, which
        is what makes the two words the right ones."""
        for command, expected in (("robot_hand_park", 0), ("robot_hand_retract", POSITION_MAX)):
            stroke = RobotHandState()
            set_amplitude(stroke, 0)
            set_center(stroke, HOLD_CENTERS[command])

            at_every_phase = {
                phase_to_position(
                    phase / 8, amplitude=stroke.amplitude, center=stroke.center
                )
                for phase in range(8)
            }
            assert at_every_phase == {expected}, command


class TestRelease:
    def test_the_dials_go_back_before_cruise_is_re_armed(self):
        """Cruise draws its waves from whatever the dials say on its first tick,
        so armed first it would take over the parked stroke and wander away from
        there instead of from what the speaker had."""
        assert release_commands(
            StrokeDials(cruise=True, speed=40, amplitude=70, center=55)
        ) == ("AMP 70", "CENTER 55", "SPEED 40", "CRUISE_ON")

    def test_cruise_is_asserted_off_as_well_as_on(self):
        """A speaker who reached for cruise while parked meant it for the parked
        stroke, not for the one coming back."""
        assert release_commands(
            StrokeDials(cruise=False, speed=50, amplitude=100, center=50)
        )[-1] == "CRUISE_OFF"

    def test_a_release_reproduces_the_stroke_the_hold_took_away(self):
        """End to end on the arithmetic: run a stroke's dials through the hold and
        then the release, and the device is back where it was at every phase."""
        before = RobotHandState()
        set_amplitude(before, 60)
        set_center(before, 45)
        set_speed(before, 35)
        was = {
            phase_to_position(phase / 8, amplitude=before.amplitude, center=before.center)
            for phase in range(8)
        }

        after = RobotHandState()
        set_amplitude(after, 0)  # the hold
        set_center(after, HOLD_CENTERS["robot_hand_retract"])
        set_amplitude(after, before.amplitude)  # the release
        set_center(after, before.center)
        set_speed(after, before.speed)

        assert after.amplitude == before.amplitude
        assert after.center == before.center
        assert after.speed == before.speed
        assert {
            phase_to_position(phase / 8, amplitude=after.amplitude, center=after.center)
            for phase in range(8)
        } == was


class TestSnapshotFileFormat:
    def test_dials_survive_the_round_trip(self):
        dials = StrokeDials(cruise=True, speed=25, amplitude=80, center=30)
        assert parse_dials(dials_text(dials)) == dials

    def test_a_snapshot_that_is_not_whole_reads_as_nothing_to_put_back(self):
        """None and an absent file mean the same thing, so a release with no hold
        behind it is one no-op rather than two kinds of one."""
        assert parse_dials("") is None
        assert parse_dials("cruise=1\nspeed=25\n") is None  # no amplitude/center
        assert parse_dials("cruise=1\nspeed=x\namplitude=80\ncenter=30\n") is None
