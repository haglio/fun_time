from __future__ import annotations

import pytest

from fun_time_vr.player import CONTROLLER_DEADZONE, TILT_RATE_DEG_S, tilt_from_stick
from fun_time_vr.vr_session import TILT_BINDINGS


class TestTiltFromStick:
    def test_a_resting_stick_moves_nothing(self):
        assert tilt_from_stick(0.0, 1.0) == 0.0

    def test_the_deadzone_swallows_a_drifting_stick(self):
        assert tilt_from_stick(CONTROLLER_DEADZONE, 1.0) == 0.0
        assert tilt_from_stick(-CONTROLLER_DEADZONE, 1.0) == 0.0

    def test_just_past_the_deadzone_moves(self):
        assert tilt_from_stick(CONTROLLER_DEADZONE + 0.01, 1.0) > 0.0

    def test_a_full_push_covers_the_rate_in_a_second(self):
        assert tilt_from_stick(1.0, 1.0) == pytest.approx(TILT_RATE_DEG_S)

    def test_the_swing_is_per_second_not_per_frame(self):
        one_frame = tilt_from_stick(1.0, 1 / 72)
        assert one_frame == pytest.approx(TILT_RATE_DEG_S / 72)
        assert sum(tilt_from_stick(1.0, 1 / 72) for _ in range(72)) == pytest.approx(
            TILT_RATE_DEG_S
        )

    def test_pushing_away_raises_and_pulling_back_lowers(self):
        assert tilt_from_stick(0.8, 0.5) > 0
        assert tilt_from_stick(-0.8, 0.5) == pytest.approx(-tilt_from_stick(0.8, 0.5))


class TestTiltBindings:
    def test_every_profile_binds_the_right_hand_axis(self):
        assert TILT_BINDINGS, "no controller would tilt anything"
        for profile, path in TILT_BINDINGS:
            assert profile.startswith("/interaction_profiles/")
            assert path.startswith("/user/hand/right/input/")
            assert path.endswith("/y")

    def test_the_suite_covers_the_headsets_this_family_meets(self):
        profiles = {profile for profile, _ in TILT_BINDINGS}
        assert any("oculus" in p for p in profiles)
        assert any("valve/index" in p for p in profiles)
        assert any("htc/vive" in p for p in profiles)

    def test_no_profile_is_bound_twice(self):
        profiles = [profile for profile, _ in TILT_BINDINGS]
        assert len(profiles) == len(set(profiles))
