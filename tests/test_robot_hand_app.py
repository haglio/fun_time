"""Tests for fun_time.robot_hand.app runtime commands."""
from __future__ import annotations

import pytest

from fun_time.robot_hand.app import (
    LEGACY_QUARTER_CYCLE_OFFSET_COMMAND,
    QUARTER_CYCLE_OFFSET_COMMAND,
    apply_runtime_command,
)
from fun_time.robot_hand.engine import PlaybackEngine


class TestApplyRuntimeCommand:
    def test_prev_steps_backward(self):
        steps: list[int] = []
        engine = {"phase": 0.0}
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            "PREV",
            engine=engine,
            rh_paused=rh_paused,
            step_clip=steps.append,
        )

        assert handled is True
        assert steps == [-1]

    def test_next_steps_forward(self):
        steps: list[int] = []
        engine = {"phase": 0.0}
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            "NEXT",
            engine=engine,
            rh_paused=rh_paused,
            step_clip=steps.append,
        )

        assert handled is True
        assert steps == [1]

    def test_offset_quarter_cycle_advances_phase(self):
        engine = {"phase": 0.1}
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            QUARTER_CYCLE_OFFSET_COMMAND,
            engine=engine,
            rh_paused=rh_paused,
            step_clip=lambda _step: None,
        )

        assert handled is True
        assert engine["phase"] == pytest.approx(0.35)

    def test_offset_quarter_cycle_wraps_phase(self):
        engine = {"phase": 0.9}
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            QUARTER_CYCLE_OFFSET_COMMAND,
            engine=engine,
            rh_paused=rh_paused,
            step_clip=lambda _step: None,
        )

        assert handled is True
        assert engine["phase"] == pytest.approx(0.15)

    def test_legacy_nudge25_command_still_supported(self):
        engine = {"phase": 0.0}
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            LEGACY_QUARTER_CYCLE_OFFSET_COMMAND,
            engine=engine,
            rh_paused=rh_paused,
            step_clip=lambda _step: None,
        )

        assert handled is True
        assert engine["phase"] == pytest.approx(0.25)

    def test_pause_sets_paused(self):
        engine = {"phase": 0.0}
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            "PAUSE",
            engine=engine,
            rh_paused=rh_paused,
            step_clip=lambda _step: None,
        )

        assert handled is True
        assert rh_paused["value"] is True

    def test_resume_clears_paused(self):
        engine = {"phase": 0.0}
        rh_paused = {"value": True}

        handled = apply_runtime_command(
            "RESUME",
            engine=engine,
            rh_paused=rh_paused,
            step_clip=lambda _step: None,
        )

        assert handled is True
        assert rh_paused["value"] is False

    def test_unknown_command_is_ignored(self):
        engine = {"phase": 0.4}
        rh_paused = {"value": False}
        steps: list[int] = []

        handled = apply_runtime_command(
            "UNKNOWN",
            engine=engine,
            rh_paused=rh_paused,
            step_clip=steps.append,
        )

        assert handled is False
        assert engine["phase"] == 0.4
        assert rh_paused["value"] is False
        assert steps == []

    def test_offset_quarter_cycle_supports_engine_object(self):
        engine = PlaybackEngine(phase=0.9, last_tick=0.0)
        rh_paused = {"value": False}

        handled = apply_runtime_command(
            QUARTER_CYCLE_OFFSET_COMMAND,
            engine=engine,
            rh_paused=rh_paused,
            step_clip=lambda _step: None,
        )

        assert handled is True
        assert engine.phase == pytest.approx(0.15)
