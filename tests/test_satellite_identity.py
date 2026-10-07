"""What a satellite window calls itself, and whose icon it wears."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pygame

from fun_time.players import Player
from fun_time.project_paths import PROJECT_ICON
from fun_time.window_layout import WindowRect
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
    launch_core_apps,
)
from main_player.player_window import wear_the_icon
from tests.test_windows_bridge_startup import _slot


def test_a_session_hands_each_satellite_fun_times_own_icon(tmp_path: Path):
    """Without one, pygame supplies its own logo and a satellite's Alt-Tab entry
    reads as some unrelated program rather than part of this session."""
    rect = WindowRect(x=0, y=0, width=100, height=100)

    with patch("fun_time.windows_bridge_startup.launch_satellite",
               side_effect=[202, 303]) as launched:
        launch_core_apps(
            python_exe="python.exe", satellite_module="satellite",
            portrait=_slot(Player.PORTRAIT, str(tmp_path / "p"), tmp_path, rect),
            landscape=_slot(Player.LANDSCAPE, str(tmp_path / "l"), tmp_path, rect),
            result_file=tmp_path / "core_apps.ini",
        )

    assert [call.kwargs["placement"].icon for call in launched.call_args_list] == [
        PROJECT_ICON, PROJECT_ICON]


def test_fun_times_icon_loads_as_a_window_icon():
    """A satellite draws no pygame surface of its own, so a broken icon would go
    unnoticed until the taskbar showed the wrong thing."""
    worn = []

    wear_the_icon(SimpleNamespace(display=SimpleNamespace(set_icon=worn.append),
                                  image=pygame.image), PROJECT_ICON)

    assert len(worn) == 1


def test_each_side_names_the_player_it_is():
    assert SATELLITE_PORTRAIT_TITLE == "Portrait AI Player"
    assert SATELLITE_LANDSCAPE_TITLE == "Landscape AI Player"
