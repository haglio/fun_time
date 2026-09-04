"""The console panel Fun Time publishes for the main player's HUD to draw."""
from __future__ import annotations

import json

from fun_time.nau_console import (
    OSR2_AUTO,
    OSR2_FUNSCRIPT,
    OSR2_GENAU,
    OSR2_IDLE,
    OSR2_OFF,
    console_payload,
    osr2_state,
)
from fun_time.player_status import GenauStatus


def _payload(**overrides) -> dict:
    base = dict(mode="nau", active=False, osr2_mode="controlled",
                funscript_driving=False, broker=False, nau_locked=True,
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

    def test_a_nau_parked_off_screen_cannot_claim_the_device(self):
        """The reported hole: in genau mode Nau is paused off screen, but its
        status file still describes the scripted video it was last showing —
        so this said "funscript" while Genau had the device, which dims every
        control on the drive readout and refuses every press on it."""
        assert osr2_state(mode="genau", osr2_mode="controlled",
                          funscript_driving=True) == OSR2_GENAU


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

    def test_carries_naus_loop_machine_for_the_record_button(self):
        """The console is drawn in genau mode too, by a player with no loop machine
        to ask — so where Nau is in the gesture rides here with the rest of the
        room, and the record button can say which press comes next."""
        assert _payload(record="recording")["record"] == "recording"
        assert _payload()["record"] == "normal"

    def test_the_lock_reported_is_the_lock_of_whoever_is_showing(self):
        """One padlock on the console, so one flag: Nau's hold on its video where
        Nau is on screen, Genau's hold on its clip where Genau is.  Publishing
        both is what left Hybrid drawing two locks that meant different things."""
        held_clip = GenauStatus(locked=True)
        loose_clip = GenauStatus(locked=False)

        for mode in ("nau", "hybrid"):
            assert _payload(mode=mode, nau_locked=True, genau=loose_clip)["locked"] is True
            assert _payload(mode=mode, nau_locked=False, genau=held_clip)["locked"] is False

        assert _payload(mode="genau", nau_locked=False, genau=held_clip)["locked"] is True
        assert _payload(mode="genau", nau_locked=True, genau=loose_clip)["locked"] is False

    def test_genaus_own_arming_and_hold_are_no_longer_published(self):
        """They were two flags for one behavior, and the padlock they fed sat
        beside Nau's on the same console."""
        payload = _payload()

        assert "auto_advance" not in payload
        assert "clip_locked" not in payload


def test_the_panel_carries_the_main_players_browse_order():
    """Latest and Shuffle are the orchestrator's to set — a spoken word or a key it
    owns — and Nau cannot tell which way round the playlist it was handed was built,
    so the order rides the panel exactly as F-mode does."""
    assert _payload(latest=True)["latest"] is True
    assert _payload()["latest"] is False


def test_the_order_reported_is_the_order_of_whoever_is_showing():
    """One slot on the console, so one flag, resolved the way the padlock is: Nau's
    playlist order where Nau is on screen, the order Genau last rescanned its clips
    folder in where Genau is.  They are separate flags because a Genau reorder
    rewrites nothing of Nau's — reporting Nau's in genau mode said "Shuffle" at
    someone who had just asked Genau for the latest."""
    for mode in ("nau", "hybrid"):
        assert _payload(mode=mode, latest=True, genau_latest=False)["latest"] is True
        assert _payload(mode=mode, latest=False, genau_latest=True)["latest"] is False

    assert _payload(mode="genau", latest=False, genau_latest=True)["latest"] is True
    assert _payload(mode="genau", latest=True, genau_latest=False)["latest"] is False


class TestTheReadoutTheWordLeaves:
    """The panel this module publishes and the drive readout a press lands on,
    joined up.

    Kept together because the failure lived in the seam and neither half could
    see it: this module was tested for the *word* it publishes and the console
    painter for what it does with a word handed to it, so a genau-mode session
    publishing "funscript" — the console's word for "somebody else has the
    device" — passed both suites while every ± mark and every draggable band on
    Genau's own readout silently refused to be pressed.
    """

    @staticmethod
    def _readout(payload: dict, tmp_path):
        """The painter, fed *payload* the way the player is fed it, with a live
        stroke on the readout; plus where the panel sits in the window."""
        from player_core.console import read_console
        from player_core.console_hud import ConsoleHud, ConsolePainter, hud_xy
        from player_core.drive_readout import DriveHud

        panel = tmp_path / "nau_console.json"
        panel.write_text(json.dumps(payload), encoding="utf-8")
        console = read_console(panel)
        assert console is not None

        painter = ConsolePainter()
        painter.rgba(ConsoleHud(
            console=console,
            drive=DriveHud(speed=50, amplitude=60, center=50,
                           waveform=tuple(0.5 for _ in range(80))),
        ))
        return painter, hud_xy()

    @staticmethod
    def _center(rect, origin):
        left, top = origin
        x, y, w, h = rect
        return left + x + w // 2, top + y + h // 2

    def test_genaus_marks_and_bands_answer_a_press_in_genau_mode(self, tmp_path):
        """Even with the video Nau is parked on carrying a funscript: Nau is not
        on screen there, so nothing of its is driving and Genau's controls are
        live.  This is the reported bug — 20 presses to move one level, because
        19 of them landed on a readout dimmed by a paused player's playlist."""
        painter, origin = self._readout(
            _payload(mode="genau", funscript_driving=True), tmp_path)

        marks = {b.action: r for r, b in painter.buttons
                 if b.action.startswith(("genau_amplitude", "genau_center", "genau_speed"))}
        assert marks, "the readout drew no marks to press"
        for action, rect in marks.items():
            assert painter.press_at(*self._center(rect, origin)) == action

        for track in painter.tracks:
            posted = painter.press_at(*self._center(track.rect, origin))
            assert posted.startswith(f"genau_{track.axis}_"), (
                f"the {track.axis} band refused a press: {posted!r}")

    def test_a_funscripts_own_turn_still_refuses_the_readout(self, tmp_path):
        """The other half of the rule, and the reason for it: in hybrid the two
        drivers take turns on one device, and adjusting a stroke Genau is not
        sending is what put both of them on it at once."""
        painter, origin = self._readout(
            _payload(mode="hybrid", funscript_driving=True), tmp_path)

        for track in painter.tracks:
            assert painter.press_at(*self._center(track.rect, origin)) == ""
        for rect, button in painter.buttons:
            if button.action.startswith(("genau_amplitude", "genau_center")):
                assert painter.press_at(*self._center(rect, origin)) == ""
