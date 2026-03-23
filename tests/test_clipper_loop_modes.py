from __future__ import annotations

from fun_time.robot_hand.clipper.loop_modes import LOOP_MODE_LABELS, LOOP_MODES


class TestLoopModes:
    def test_labels_cover_all_modes(self):
        assert set(LOOP_MODE_LABELS) == set(LOOP_MODES)

    def test_mode_names_are_unique(self):
        assert len(LOOP_MODES) == len(set(LOOP_MODES))
