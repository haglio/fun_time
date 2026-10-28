"""The OSR2 part the Dashboard draws, and the OSR2 button every Funestra wears."""
from __future__ import annotations

import pytest
from app_support.osr2_device import Osr2Mode
from funestra.console import (
    OSR2_CONTROL_BUTTONS,
    OSR2_DRIVING,
    OSR2_PARKED,
    OSR2_RETRACTED,
    console_text,
    read_console,
)
from funestra.console_hud import ConsoleHud, ConsolePainter, hud_xy
from funestra.drive_layout import MAX_INTENSITY
from funestra.drive_readout import DriveHud
from funestra.hud_button import Button
from funestra.hud_marks import BROKER_ICON, shared_mark
from funestra.modes import MainMode, Osr2State
from funestra.robot_hand import WaveformShape

from fun_time.console_buttons import MainSlot, console_rows, osr2_rows
from fun_time.main_funestra_console import osr2_state
from fun_time.osr2_section import osr2_button, osr2_controls, osr2_part
from fun_time.room_funestras import RoomFunestra


def _part(holder: RoomFunestra = RoomFunestra.MAIN, *, main_mode: MainMode = MainMode.KINO,
          control: str = OSR2_DRIVING, learned: bool = False, shape: str = "sine"):
    return osr2_part(holder, main_mode=main_mode, osr2=Osr2State.ROBOT_HAND, control=control,
                     paused=False, learned=learned, shape=shape, broker=True, max_intensity=100)


def _button(part, action: str) -> Button:
    return next(b for row in part.osr2_rows for b in row if b.command == action)


def _actions(part) -> list[str]:
    return [b.command for row in part.osr2_rows for b in row if b.command]


class TestTheOsr2Switch:
    """One switch saying what the room is doing to the OSR2: on while driving,
    off while parked, and a press flips it."""

    def test_it_is_there_whichever_funestra_has_the_osr2_in_either_mode(self):
        for holder in RoomFunestra:
            for mode in MainMode:
                assert OSR2_CONTROL_BUTTONS[OSR2_PARKED] in _actions(_part(holder, main_mode=mode))

    def test_it_is_on_while_driving_and_a_press_parks(self):
        assert _button(_part(control=OSR2_DRIVING), OSR2_CONTROL_BUTTONS[OSR2_PARKED]).lit

    @pytest.mark.parametrize("control", [OSR2_PARKED, OSR2_RETRACTED])
    def test_it_is_off_while_held_and_a_press_drives(self, control):
        assert not _button(_part(control=control), OSR2_CONTROL_BUTTONS[OSR2_DRIVING]).lit

    def test_the_part_says_what_the_room_is_doing_to_the_osr2(self):
        assert _part(control=OSR2_PARKED).osr2_control == OSR2_PARKED


class TestTheRowThatAimsTheDevice:
    def test_human_inspired_motion_follows_the_switch_and_wears_the_stick_figure(self):
        resting = _button(_part(), "robot_hand_toggle_learned")
        lit = _button(_part(learned=True), "robot_hand_toggle_learned")

        assert resting.glyph == shared_mark("human")
        assert (resting.lit, lit.lit) == (False, True)
        assert (lit.favorite, lit.warn, lit.hold) == (False, False, False)

    def test_the_waveform_button_names_the_waveform(self):
        assert _button(_part(shape="rounded_square"), "robot_hand_cycle_shape").tooltip == "Waveform: Square"

    def test_the_waveform_button_wears_the_waveform_the_motion_is_in(self):
        faces = {_button(_part(shape=shape.value), "robot_hand_cycle_shape").glyph
                 for shape in WaveformShape}

        assert len(faces) == len(WaveformShape)

    def test_rides_on_the_dashboard_s_part_never_on_the_console(self):
        slot = MainSlot(main_mode=MainMode.KINO)
        console = [b.command for row in (*console_rows(slot), *osr2_rows(slot)) for b in row]

        assert "robot_hand_park" in _actions(_part())
        assert "robot_hand_park" not in console


class TestTheReadoutTheWordLeaves:
    """The part the feed publishes and the drive readout a press lands on,
    joined up.

    Kept together because the failure lived in the seam and neither half could
    see it: the word published was tested on its own and the painter for what
    it does with a word handed to it, so a genau-mode session publishing
    "funscript" -- the word for "somebody else has the device" -- passed both
    suites while every mark and every draggable band on Genau's own readout
    silently refused to be pressed.
    """

    @staticmethod
    def _readout(main_mode: MainMode, *, funscript_driving: bool, max_intensity: int = 100,
                 tmp_path):
        part = osr2_part(
            RoomFunestra.MAIN, main_mode=main_mode, control=OSR2_DRIVING, paused=False,
            osr2=osr2_state(main_mode=main_mode, osr2_mode=Osr2Mode.CONTROLLED,
                            funscript_driving=funscript_driving),
            learned=False, shape="sine", broker=True, max_intensity=max_intensity)
        published = tmp_path / "dashboard_osr2.json"
        published.write_text(console_text(part), encoding="utf-8")
        painter = ConsolePainter(device_only=True)
        painter.rgba(ConsoleHud(console=read_console(published), drive=DriveHud(
            speed=50, amplitude=60, center=50, waveform=tuple(0.5 for _ in range(80)))))
        return painter

    @staticmethod
    def _readouts_own(painter):
        return [track for track in painter.tracks
                if track.axis != MAX_INTENSITY and track.handle_of is None]

    @staticmethod
    def _center(rect):
        left, top = hud_xy()
        x, y, w, h = rect
        return left + x + w // 2, top + y + h // 2

    def test_genaus_marks_and_bands_answer_a_press_in_genau_mode(self, tmp_path):
        """Even with the video the Main Funestra is parked on carrying a funscript:
        the Main Funestra is not on screen there, so nothing of its is driving and
        Genau's controls are live."""
        painter = self._readout(MainMode.GENAU, funscript_driving=True, tmp_path=tmp_path)

        marks = {b.command: r for r, b in painter.buttons
                 if b.command.startswith(("robot_hand_amplitude", "robot_hand_center", "robot_hand_speed"))}
        assert marks, "the readout drew no marks to press"
        for action, rect in marks.items():
            assert painter.press_at(*self._center(rect)) == action
        for track in self._readouts_own(painter):
            painter.press_at(*self._center(track.rect))
            assert painter.holding, f"the {track.axis} band at {track.rect} refused a press"

    def test_a_funscripts_own_turn_still_refuses_the_readout(self, tmp_path):
        """In kino mode the two drivers take turns on one device, and adjusting a
        motion Genau is not sending is what put both of them on it at once."""
        painter = self._readout(MainMode.KINO, funscript_driving=True, tmp_path=tmp_path)

        for track in self._readouts_own(painter):
            assert painter.press_at(*self._center(track.rect)) == ""
            assert not painter.holding

    def test_the_max_intensity_holds_a_funscripts_turn_down_too_so_it_stays_pressable(self, tmp_path):
        painter = self._readout(MainMode.KINO, funscript_driving=True, max_intensity=50,
                                tmp_path=tmp_path)
        (max_intensity,) = [track for track in painter.tracks if track.axis == MAX_INTENSITY]

        assert painter.press_at(*self._center(max_intensity.rect)).startswith("max_intensity_")


class TestTheBroker:
    """The device's own control: the broker that talks to the OSR2 at all."""

    def test_is_a_control_and_says_which_way_a_press_goes(self):
        (running,), (down,) = osr2_controls(broker=True), osr2_controls(broker=False)

        assert running.command == down.command == "broker_panel"
        assert running.lit is True
        assert down.lit is False and down.warn is True
        assert (running.tooltip, down.tooltip) == (
            "Broker is running — press to stop it",
            "Broker is not running — press to start it",
        )

    def test_asks_for_its_own_icon_rather_than_the_word(self):
        (broker,) = osr2_controls(broker=True)

        assert broker.glyph == BROKER_ICON


class TestTheOsr2Button:
    def test_is_lit_on_the_funestra_that_has_the_osr2_and_says_so(self):
        button = osr2_button(RoomFunestra.PORTRAIT, has_it=True)

        assert button.lit is True
        assert button.tooltip == "This Funestra has the OSR2 — it follows this Funestra's funscripts"

    def test_offers_the_osr2_on_every_other_funestra(self):
        button = osr2_button(RoomFunestra.LANDSCAPE, has_it=False)

        assert (button.command, button.lit) == ("landscape_take_osr2", False)
        assert button.tooltip == "Take over the OSR2 — it follows this Funestra's funscripts"
