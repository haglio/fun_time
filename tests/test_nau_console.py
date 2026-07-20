"""The console panel Fun Time publishes for Nau's HUD to draw."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.dashboard_runtime import GenauStatus
from fun_time.nau_console import (
    OSR2_AUTO,
    OSR2_FUNSCRIPT,
    OSR2_GENAU,
    OSR2_IDLE,
    OSR2_OFF,
    console_payload,
    osr2_label,
)


def _payload(**overrides) -> dict:
    base = dict(mode="nau", osr2_mode="controlled", primary_path="",
                takeover_allowed=True, genau=GenauStatus())
    base.update(overrides)
    return console_payload(**base)


class TestOsr2Label:
    """The dashboard drew this as a box with a cable to the primary player; the
    console says it in a line, because a cable between two things in the same
    panel says nothing."""

    def test_the_devices_own_modes_answer_whatever_is_playing(self):
        for osr2_mode, expected in (("off", OSR2_OFF), ("auto", OSR2_AUTO)):
            assert osr2_label(mode="hybrid", osr2_mode=osr2_mode,
                              primary_path="C:/v/scripted.mp4") == expected

    def test_a_scripted_video_drives_the_device_itself(self):
        with patch("fun_time.nau_console.has_matching_funscript", return_value=True):
            assert osr2_label(mode="hybrid", osr2_mode="controlled",
                              primary_path="C:/v/a.mp4") == OSR2_FUNSCRIPT

    def test_without_a_funscript_genau_has_it_if_genau_is_there(self):
        """The difference between "idle" and "Genau has it" is whether a waveform
        is running at all — which is the mode, not the video."""
        with patch("fun_time.nau_console.has_matching_funscript", return_value=False):
            assert osr2_label(mode="hybrid", osr2_mode="controlled",
                              primary_path="C:/v/a.mp4") == OSR2_GENAU
            assert osr2_label(mode="nau", osr2_mode="controlled",
                              primary_path="C:/v/a.mp4") == OSR2_IDLE


class TestPayload:
    def test_carries_the_mode_the_console_lights_a_button_for(self):
        assert _payload(mode="hybrid")["mode"] == "hybrid"

    def test_names_the_limits_genau_has_run_into(self):
        """The console greys a control out at the end of its range, so it needs
        the ends by name — a flag per control end, exactly as Genau reports them."""
        payload = _payload(genau=GenauStatus(amp_at_max=True, spd_at_min=True))

        assert payload["limits"] == ["amp_max", "spd_min"]
        assert _payload()["limits"] == []

    def test_carries_genaus_own_state_for_the_controls_that_show_it(self):
        payload = _payload(genau=GenauStatus(cruise_active=True, shape="sawtooth"),
                           takeover_allowed=False)

        assert payload["cruise"] is True
        assert payload["shape"] == "sawtooth"
        assert payload["takeover_allowed"] is False
