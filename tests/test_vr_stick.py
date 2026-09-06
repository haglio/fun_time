from __future__ import annotations

import pytest

from fun_time_vr.player import CONTROLLER_DEADZONE, TILT_RATE_DEG_S, tilt_from_stick
from fun_time_vr.pointer import LEFT, RIGHT
from fun_time_vr.roles import TILT_STEP_DEG
from fun_time_vr.vr_session import AIM, CONTROLLER_BINDINGS, TILT, TRIGGER


class TestTiltFromStick:
    def test_a_resting_stick_moves_nothing(self):
        assert tilt_from_stick(0.0, 1.0) == 0.0

    def test_the_deadzone_swallows_a_drifting_stick(self):
        assert tilt_from_stick(CONTROLLER_DEADZONE, 1.0) == 0.0
        assert tilt_from_stick(-CONTROLLER_DEADZONE, 1.0) == 0.0

    def test_just_past_the_deadzone_moves(self):
        assert tilt_from_stick(CONTROLLER_DEADZONE + 0.01, 1.0) < 0.0

    def test_a_full_push_covers_the_rate_in_a_second(self):
        assert tilt_from_stick(1.0, 1.0) == pytest.approx(-TILT_RATE_DEG_S)

    def test_the_swing_is_per_second_not_per_frame(self):
        one_frame = tilt_from_stick(1.0, 1 / 72)
        assert one_frame == pytest.approx(-TILT_RATE_DEG_S / 72)
        assert sum(tilt_from_stick(1.0, 1 / 72) for _ in range(72)) == pytest.approx(
            -TILT_RATE_DEG_S
        )

    def test_pushing_away_lowers_and_pulling_back_raises(self):
        assert tilt_from_stick(0.8, 0.5) < 0
        assert tilt_from_stick(-0.8, 0.5) == pytest.approx(-tilt_from_stick(0.8, 0.5))

    def test_the_stick_runs_opposite_to_the_verbs(self):
        # Both inputs write the same tilt, and the inversion is only the
        # stick's: PgUp still raises, so the stick pushed away must lower.
        assert TILT_STEP_DEG > 0
        assert tilt_from_stick(1.0, 1.0) < 0


class TestControllerBindings:
    def test_every_profile_that_tilts_does_so_on_the_right_hands_y_axis(self):
        tilting = [bindings[TILT] for bindings in CONTROLLER_BINDINGS.values() if TILT in bindings]
        assert tilting, "no controller would tilt anything"
        for paths in tilting:
            (path,) = paths
            assert path.startswith("/user/hand/right/input/")
            assert path.endswith("/y")

    def test_every_profile_points_and_squeezes_with_either_hand(self):
        for profile, bindings in CONTROLLER_BINDINGS.items():
            assert profile.startswith("/interaction_profiles/")
            for action in (AIM, TRIGGER):
                hands = {path.split("/")[3] for path in bindings[action]}
                assert hands == {LEFT, RIGHT}, f"{profile} binds {action} for {hands}"
            assert all(path.endswith("/aim/pose") for path in bindings[AIM])

    def test_the_suite_covers_the_headsets_this_family_meets(self):
        profiles = set(CONTROLLER_BINDINGS)
        assert any("oculus" in p for p in profiles)
        assert any("valve/index" in p for p in profiles)
        assert any("htc/vive" in p for p in profiles)
