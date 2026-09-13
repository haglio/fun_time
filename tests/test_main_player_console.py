"""The console panel Fun Time publishes for the main player's HUD to draw."""
from __future__ import annotations

from player_core.console import ConsoleModel, console_text, parse_console
from player_core.hud_button import Button

from fun_time.main_player_console import (
    OSR2_AUTO,
    OSR2_FUNSCRIPT,
    OSR2_OFF,
    OSR2_ROBOT_HAND,
    console_model,
    osr2_state,
)
from fun_time.player_status import GenauStatus, MainPlayerStatus


def _payload(**overrides) -> ConsoleModel:
    base = dict(mode="video", active=False, osr2_mode="controlled", broker=False,
                main_player=MainPlayerStatus(), genau=GenauStatus())
    base.update(overrides)
    return console_model(**base)


def _button(model: ConsoleModel, action: str) -> Button:
    return next(b for row in model.rows for b in row if b.action == action)


def _actions(model: ConsoleModel) -> list[str]:
    return [b.action for row in model.rows for b in row if b.action]


class TestOsr2State:
    """What has the device, as one compact word — the console badges it."""

    def test_the_devices_own_modes_answer_whatever_is_playing(self):
        for osr2_mode, expected in (("off", OSR2_OFF), ("auto", OSR2_AUTO)):
            assert osr2_state(mode="video", osr2_mode=osr2_mode,
                              funscript_driving=True) == expected

    def test_a_funscript_that_is_actually_driving_says_so(self):
        assert osr2_state(mode="video", osr2_mode="controlled",
                          funscript_driving=True) == OSR2_FUNSCRIPT

    def test_a_scripted_videos_quiet_stretch_reads_as_the_robot_hand_not_funscript(self):
        """The reported bug: on a rest gap of a scripted video the Robot Hand drives, but
        it said funscript because a funscript merely *existed*.  It is the driving
        state that decides now, not the file's presence."""
        assert osr2_state(mode="video", osr2_mode="controlled",
                          funscript_driving=False) == OSR2_ROBOT_HAND

    def test_without_a_driver_the_robot_hand_has_the_device_in_either_mode(self):
        for mode in ("video", "genau"):
            assert osr2_state(mode=mode, osr2_mode="controlled",
                              funscript_driving=False) == OSR2_ROBOT_HAND

    def test_a_main_player_parked_off_screen_cannot_claim_the_device(self):
        """The reported bug: in genau mode the main player is paused off screen, but its
        status file still describes the scripted video it was last showing —
        so this said "funscript" while Genau had the device, which dims every
        control on the drive readout and refuses every press on it."""
        assert osr2_state(mode="genau", osr2_mode="controlled",
                          funscript_driving=True) == OSR2_ROBOT_HAND


class TestPayload:
    def test_carries_the_room_the_player_cannot_see(self):
        payload = _payload(mode="video", active=True, osr2_mode="auto")

        assert payload.mode == "video"
        assert payload.active is True
        assert payload.osr2 == OSR2_AUTO

    def test_the_osr2_lines_control_is_the_broker_lit_while_it_runs(self):
        """Broker status moved off the dashboard onto this panel: one button on
        the OSR2 line, blue while the service is up, red while it is down."""
        (running,), (stopped,) = _payload(broker=True).osr2_controls, _payload().osr2_controls

        assert running.action == stopped.action == "broker_panel"
        assert running.lit and not running.warn and "stop" in running.tooltip
        assert stopped.warn and not stopped.lit and "start" in stopped.tooltip

    def test_the_osr2_state_is_read_off_the_main_players_own_funscript(self):
        driving = MainPlayerStatus(has_funscript=True)

        assert _payload(mode="video", main_player=driving).osr2 == OSR2_FUNSCRIPT
        assert _payload(mode="genau", main_player=driving).osr2 == OSR2_ROBOT_HAND

    def test_declares_genaus_own_switches_on_the_control_row(self):
        payload = _payload(genau=GenauStatus(cruise_active=True, shape="sawtooth"))

        assert _button(payload, "robot_hand_toggle_cruise").lit is True
        assert _button(payload, "robot_hand_cycle_shape").tooltip == "Waveform: Sawtooth"

    def test_declares_the_main_players_loop_machine_on_the_record_button(self):
        """The console is drawn in genau mode too, by a player with no loop machine
        to ask — so where the main player is in the gesture rides here with the rest of the
        room, and the record button can say which press comes next."""
        recording = _button(_payload(main_player=MainPlayerStatus(state="recording")),
                            "main_player_record_tap")
        resting = _button(_payload(), "main_player_record_tap")

        assert recording.warn and "out point" in recording.tooltip
        assert not resting.warn and not resting.hold

    def test_the_lock_reported_is_the_lock_of_whoever_is_showing(self):
        """One padlock on the console, so one flag: the main player's hold on its video where
        The main player is on screen, Genau's hold on its clip where Genau is.  Publishing
        both is what left video mode drawing two locks that meant different things."""
        held_clip, loose_clip = GenauStatus(locked=True), GenauStatus(locked=False)
        held_video, loose_video = MainPlayerStatus(locked=True), MainPlayerStatus(locked=False)

        for mode, main_player, genau, expected in (
            ("video", held_video, loose_clip, True),
            ("video", loose_video, held_clip, False),
            ("genau", loose_video, held_clip, True),
            ("genau", held_video, loose_clip, False),
        ):
            payload = _payload(mode=mode, main_player=main_player, genau=genau)
            assert payload.locked is expected
            assert _button(payload, "main_lock").lit is expected

    def test_genaus_pace_is_named_on_its_lock(self):
        """Genau publishes how long an unheld clip stays up, and the lock in genau
        mode says so on hover -- the only place the number is spelled out."""
        payload = _payload(mode="genau", genau=GenauStatus(locked=False), genau_pace_s=7)

        assert "every 7s" in _button(payload, "main_lock").tooltip

    def test_declares_what_the_main_player_published_about_its_video(self):
        """The compilation, version and clip-jump buttons, and the length pair,
        are lit and named from the main player's own status lines: only it
        knows them, and a player that says nothing leaves them dim."""
        known = _payload(main_player=MainPlayerStatus(
            length_mode="full", compilation="Vol 3", has_compilation=True,
            has_other_versions=True, jump_to="scene"))
        unknown = _payload()

        assert any(a.startswith("main_player_length") for a in _actions(known))
        assert not any(a.startswith("main_player_length") for a in _actions(unknown))
        assert _button(known, "main_player_end_compilation").lit is True
        assert _button(unknown, "main_player_compilation").dim is True
        assert _button(known, "main_player_cycle_version").dim is False
        assert _button(unknown, "main_player_cycle_version").dim is True
        assert _button(known, "main_player_full_vid").dim is False
        assert _button(unknown, "main_player_clip_jump").dim is True


def test_the_panel_carries_the_main_players_browse_order():
    """Latest and Shuffle are the orchestrator's to set — a spoken word or a key it
    owns — and the main player cannot tell which way round the playlist it was handed was built,
    so the order rides the panel exactly as F-mode does."""
    assert _payload(latest=True).latest is True
    assert _payload().latest is False
    assert _button(_payload(latest=True), "main_latest").lit is True
    assert _button(_payload(), "main_shuffle").lit is True


def test_f_mode_lights_off_what_the_orchestrator_holds():
    assert _button(_payload(f_mode=True), "main_fmode").lit is True
    assert _button(_payload(), "main_fmode").lit is False


def test_the_order_reported_is_the_order_of_whoever_is_showing():
    """One slot on the console, so one flag, resolved the way the padlock is: the main player's
    playlist order where the main player is on screen, the order Genau last rescanned its clips
    folder in where Genau is.  They are separate flags because a Genau reorder
    rewrites nothing of the main player's — reporting the main player's in genau mode said "Shuffle" at
    someone who had just asked Genau for the latest."""
    assert _payload(mode="video", latest=True, genau_latest=False).latest is True
    assert _payload(mode="video", latest=False, genau_latest=True).latest is False

    assert _payload(mode="genau", latest=False, genau_latest=True).latest is True
    assert _payload(mode="genau", latest=True, genau_latest=False).latest is False


def test_the_panel_says_which_shapes_of_video_the_browse_may_reach():
    """Two flags with a third answer: None where the rotation holds one shape,
    which is every session outside the headset, and the console then draws no pair
    of buttons for a choice there is none to make."""
    assert not any(a.startswith("main_projection") for a in _actions(_payload()))

    headset = _payload(plays_vr=True, plays_flat=False)
    assert _button(headset, "main_projection_none").lit is True     # the VR button, lit
    assert _button(headset, "main_projection_both").lit is False    # the flat one, dark


def test_the_published_text_carries_the_rows_the_player_draws():
    payload = _payload(mode="genau", broker=True)

    parsed = parse_console(console_text(payload))

    assert parsed == payload
    assert parsed.rows and parsed.osr2_controls


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
    def _readout(payload: ConsoleModel, tmp_path):
        """The painter, fed *payload* the way the player is fed it, with a live
        motion on the readout; plus where the panel sits in the window."""
        from player_core.console import read_console
        from player_core.console_hud import ConsoleHud, ConsolePainter, hud_xy
        from player_core.drive_readout import DriveHud

        panel = tmp_path / "main_player_console.json"
        panel.write_text(console_text(payload), encoding="utf-8")
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
        """Even with the video the main player is parked on carrying a funscript: the main player is not
        on screen there, so nothing of its is driving and Genau's controls are
        live.  This is the reported bug — 20 presses to move one level, because
        19 of them landed on a readout dimmed by a paused player's playlist."""
        painter, origin = self._readout(
            _payload(mode="genau", main_player=MainPlayerStatus(has_funscript=True)), tmp_path)

        marks = {b.action: r for r, b in painter.buttons
                 if b.action.startswith(("robot_hand_amplitude", "robot_hand_center", "robot_hand_speed"))}
        assert marks, "the readout drew no marks to press"
        for action, rect in marks.items():
            assert painter.press_at(*self._center(rect, origin)) == action

        for track in painter.tracks:
            posted = painter.press_at(*self._center(track.rect, origin))
            assert posted.startswith(f"robot_hand_{track.axis}_"), (
                f"the {track.axis} band refused a press: {posted!r}")

    def test_a_funscripts_own_turn_still_refuses_the_readout(self, tmp_path):
        """The other half of the rule, and the reason for it: in video mode the two
        drivers take turns on one device, and adjusting a motion Genau is not
        sending is what put both of them on it at once."""
        painter, origin = self._readout(
            _payload(mode="video", main_player=MainPlayerStatus(has_funscript=True)), tmp_path)

        for track in painter.tracks:
            assert painter.press_at(*self._center(track.rect, origin)) == ""
        for rect, button in painter.buttons:
            if button.action.startswith(("genau_amplitude", "robot_hand_center")):
                assert painter.press_at(*self._center(rect, origin)) == ""

    def test_the_declared_rows_are_what_the_player_presses(self, tmp_path):
        """The whole way round: a button declared here, published, read back and
        painted, posts on the panel exactly the verb it was declared with."""
        painter, origin = self._readout(_payload(mode="video"), tmp_path)

        rect = next(r for r, b in painter.buttons if b.action == "main_next")
        assert painter.press_at(*self._center(rect, origin)) == "main_next"
