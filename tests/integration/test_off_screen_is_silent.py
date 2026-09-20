"""On a real hidden desktop, on_hidden_desktop() and the players that consult it
turn silent -- the end-to-end check the unit tests cannot make against faked names."""
from __future__ import annotations

import argparse
import sys

import pytest

from fun_time.win32_desktop import current_desktop_name, input_desktop_name, on_hidden_desktop
from main_player.cli import audio_muted as main_audio_muted
from satellite.cli import audio_muted as satellite_audio_muted

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops")


def test_a_run_here_really_is_on_a_desktop_that_is_not_the_input_one():
    assert on_hidden_desktop() is True
    assert current_desktop_name() != input_desktop_name()


def test_the_players_silence_themselves_here_without_the_mute_switch(monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    args = argparse.Namespace(no_audio=False)
    assert main_audio_muted(args) is True
    assert satellite_audio_muted(args) is True
