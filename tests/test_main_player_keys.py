"""Every key the main player's window answers, against the thing it is supposed to move.

One row per binding, naming the only state that key may change, and the whole
observable state is compared -- so a binding wired to its neighbor's action
fails on the field it should not have touched, not only on the one it missed.
Ten of them reached the player through a chain of comparisons inside the run
loop, and a chain is exactly where two of them quietly swap.
"""
from __future__ import annotations

from pathlib import Path

import pygame
import pytest

from main_player.controls import SEEK_STEP_MS
from main_player.dashboard import Dashboard
from main_player.keys import Keys


class SpySession:
    """The player, as a key press reaches it."""

    def __init__(self) -> None:
        self.pause_toggles = 0
        self.recording = 0        # +1 on the way down, -1 on the way up
        self.steps: list[int] = []
        self.seeks: list[float] = []
        self.version_cycles = 0

    def toggle_pause(self) -> None:
        self.pause_toggles += 1

    def record_down(self) -> None:
        self.recording += 1

    def record_up(self) -> None:
        self.recording -= 1

    def step(self, delta: int) -> None:
        self.steps.append(delta)

    def seek_by(self, delta_ms: float) -> None:
        self.seeks.append(delta_ms)

    def cycle_version(self) -> None:
        self.version_cycles += 1


class SpyModes:
    def __init__(self) -> None:
        self.length_toggles = 0

    def toggle_length(self) -> None:
        self.length_toggles += 1


class Keyboard:
    """A keyboard over a player, and everything a press could move."""

    def __init__(self, cmd_file: Path | None = None) -> None:
        self.session = SpySession()
        self.modes = SpyModes()
        self.keys = Keys(self.session, self.modes, Dashboard(cmd_file))

    def state(self) -> dict:
        return {
            "pause_toggles": self.session.pause_toggles,
            "recording": self.session.recording,
            "steps": tuple(self.session.steps),
            "seeks": tuple(self.session.seeks),
            "version_cycles": self.session.version_cycles,
            "length_toggles": self.modes.length_toggles,
        }





# key, and the ONLY state going down on it may move.
PRESSES = [
    ("escape", pygame.K_ESCAPE, {"pause_toggles": 1}),
    ("r", pygame.K_r, {"recording": 1}),
    ("[", pygame.K_LEFTBRACKET, {"steps": (-1,)}),
    ("]", pygame.K_RIGHTBRACKET, {"steps": (1,)}),
    ("-", pygame.K_MINUS, {"seeks": (-SEEK_STEP_MS,)}),
    ("=", pygame.K_EQUALS, {"seeks": (SEEK_STEP_MS,)}),
    ("v", pygame.K_v, {"version_cycles": 1}),
    ("l", pygame.K_l, {"length_toggles": 1}),
]


@pytest.mark.parametrize("name, key, moves", PRESSES, ids=[row[0] for row in PRESSES])
def test_a_key_moves_what_it_is_bound_to_and_nothing_else(name, key, moves):
    keyboard = Keyboard()
    before = keyboard.state()

    assert keyboard.keys.press(key) is True
    assert keyboard.state() == {**before, **moves}


class TestTheRecordKeyIsHeld:
    """Pressing marks the loop's in point and letting go marks its out point,
    so the two halves are one gesture rather than two bindings."""

    def test_letting_it_go_ends_what_pressing_it_began(self):
        keyboard = Keyboard()
        keyboard.keys.press(pygame.K_r)

        assert keyboard.keys.release(pygame.K_r) is True
        assert keyboard.state()["recording"] == 0

    def test_no_other_key_answers_being_let_go(self):
        keyboard = Keyboard()
        before = keyboard.state()

        for _name, key, _moves in PRESSES:
            if key != pygame.K_r:
                assert keyboard.keys.release(key) is False

        assert keyboard.state() == before


class TestQuitting:
    def test_ctrl_q_in_a_session_asks_the_session_and_this_player_stays(self, tmp_path):
        cmd_file = tmp_path / "dashboard_cmd.txt"
        keyboard = Keyboard(cmd_file)

        assert keyboard.keys.press(pygame.K_q, pygame.KMOD_CTRL) is True

        assert cmd_file.read_text(encoding="utf-8").split() == ["quit"]

    def test_q_on_its_own_is_not_a_quit(self, tmp_path):
        """It is the one binding that reads a modifier, and a bare q has to go
        on meaning nothing rather than ending the session."""
        cmd_file = tmp_path / "dashboard_cmd.txt"
        keyboard = Keyboard(cmd_file)

        assert keyboard.keys.press(pygame.K_q) is False
        assert not cmd_file.exists()


class TestAKeyNothingIsBoundTo:
    def test_it_is_reported_unanswered_and_moves_nothing(self):
        keyboard = Keyboard()
        before = keyboard.state()

        assert keyboard.keys.press(pygame.K_z) is False
        assert keyboard.state() == before
