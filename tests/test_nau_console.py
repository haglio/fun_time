"""The console panel Fun Time publishes for the primary player's HUD to draw."""
from __future__ import annotations

from fun_time.dashboard_runtime import GenauStatus
from fun_time.nau_console import (
    OSR2_AUTO,
    OSR2_FUNSCRIPT,
    OSR2_GENAU,
    OSR2_IDLE,
    OSR2_OFF,
    console_payload,
    osr2_state,
)


def _payload(**overrides) -> dict:
    base = dict(mode="nau", active=False, osr2_mode="controlled",
                funscript_driving=False, broker=False,
                genau=GenauStatus())
    base.update(overrides)
    return console_payload(**base)


class TestOsr2State:
    """What has the device, as one compact word — the console boxes it."""

    def test_the_devices_own_modes_answer_whatever_is_playing(self):
        for osr2_mode, expected in (("off", OSR2_OFF), ("auto", OSR2_AUTO)):
            assert osr2_state(mode="hybrid", osr2_mode=osr2_mode,
                              funscript_driving=True) == expected

    def test_a_funscript_that_is_actually_driving_says_so(self):
        assert osr2_state(mode="hybrid", osr2_mode="controlled",
                          funscript_driving=True) == OSR2_FUNSCRIPT

    def test_a_scripted_videos_quiet_stretch_reads_as_genau_not_funscript(self):
        """The reported hole: on a rest gap of a scripted video Genau drives, but
        it said funscript because a funscript merely *existed*.  It is the driving
        state that decides now, not the file's presence."""
        assert osr2_state(mode="hybrid", osr2_mode="controlled",
                          funscript_driving=False) == OSR2_GENAU

    def test_without_a_driver_it_is_idle_unless_genau_is_there(self):
        assert osr2_state(mode="nau", osr2_mode="controlled",
                          funscript_driving=False) == OSR2_IDLE
        assert osr2_state(mode="genau", osr2_mode="controlled",
                          funscript_driving=False) == OSR2_GENAU


class TestPayload:
    def test_carries_the_room_the_player_cannot_see(self):
        payload = _payload(mode="hybrid", active=True, broker=True, osr2_mode="auto")

        assert payload["mode"] == "hybrid"
        assert payload["active"] is True
        assert payload["broker"] is True
        assert payload["osr2"] == OSR2_AUTO

    def test_carries_genaus_own_switches_for_the_control_row(self):
        payload = _payload(genau=GenauStatus(cruise_active=True, shape="sawtooth"),
                           )

        assert payload["cruise"] is True
        assert payload["shape"] == "sawtooth"

    def test_a_held_clip_is_reported_apart_from_the_arming(self):
        payload = _payload(genau=GenauStatus(auto_advance_active=True, clip_locked=True))

        assert payload["auto_advance"] is True
        assert payload["clip_locked"] is True
        assert _payload()["auto_advance"] is False
