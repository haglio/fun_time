"""The off-screen check that silences a player, and the three players it silences."""
from __future__ import annotations

import argparse
import sys

import pytest

from fun_time import win32_desktop
from fun_time.win32_desktop import _is_off_screen, current_desktop_name, on_hidden_desktop


def test_a_desktop_that_is_not_the_input_one_is_off_screen():
    assert _is_off_screen("FunTimeIntegration7", "Default") is True


def test_the_input_desktop_itself_is_not_off_screen():
    assert _is_off_screen("Default", "Default") is False


def test_the_name_comparison_ignores_case():
    assert _is_off_screen("default", "Default") is False


def test_a_non_default_name_is_off_screen_when_the_input_desktop_cannot_be_named():
    # OpenInputDesktop fails on a secure desktop; the interactive one is always "Default".
    assert _is_off_screen("FunTimeIntegration7", None) is True
    assert _is_off_screen("Default", None) is False


def test_an_unreadable_own_desktop_never_silences_a_possibly_live_session():
    assert _is_off_screen(None, "Default") is False
    assert _is_off_screen(None, None) is False


def test_on_hidden_desktop_compares_this_thread_desktop_with_the_input_one(monkeypatch):
    monkeypatch.setattr(win32_desktop, "current_desktop_name", lambda: "FunTimeIntegration7")
    monkeypatch.setattr(win32_desktop, "input_desktop_name", lambda: "Default")
    assert on_hidden_desktop() is True
    monkeypatch.setattr(win32_desktop, "current_desktop_name", lambda: "Default")
    assert on_hidden_desktop() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops")
def test_the_unit_run_is_on_the_interactive_desktop_so_nothing_is_silenced():
    assert current_desktop_name() is not None
    assert on_hidden_desktop() is False


@pytest.mark.parametrize("module_name", ["main_player.cli", "satellite.cli"])
def test_a_video_player_silences_itself_off_screen_with_no_switch_set(module_name, monkeypatch):
    import importlib

    module = importlib.import_module(module_name)
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.setattr(module, "on_hidden_desktop", lambda: True)
    assert module.audio_muted(argparse.Namespace(no_audio=False)) is True
    monkeypatch.setattr(module, "on_hidden_desktop", lambda: False)
    assert module.audio_muted(argparse.Namespace(no_audio=False)) is False


def test_the_audio_companion_silences_itself_off_screen_with_no_switch_set(monkeypatch):
    from fun_time import audio_companion_app

    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.setattr(audio_companion_app, "on_hidden_desktop", lambda: True)
    assert audio_companion_app.force_muted() is True
    monkeypatch.setattr(audio_companion_app, "on_hidden_desktop", lambda: False)
    assert audio_companion_app.force_muted() is False
