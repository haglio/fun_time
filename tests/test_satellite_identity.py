"""What a satellite window calls itself, and whose icon it wears."""
from __future__ import annotations

from fun_time.dashboard_state import LABEL_LANDSCAPE, LABEL_PORTRAIT
from fun_time.windows_bridge_startup import (
    SATELLITE_LANDSCAPE_TITLE,
    SATELLITE_PORTRAIT_TITLE,
)
from satellite.app import ICON_PATH, _load_icon_surface


def test_the_icon_is_fun_times_own():
    """Without one, pygame supplies its own logo and a satellite's Alt-Tab entry
    reads as some unrelated program rather than part of this session."""
    assert ICON_PATH.name == "icon.ico"
    assert (ICON_PATH.parent / "fun_time").is_dir(), "not the repo's own icon"
    assert ICON_PATH.is_file(), f"{ICON_PATH} is missing"


def test_the_icon_loads_as_a_surface():
    """A satellite draws no pygame surface of its own, so a broken icon would go
    unnoticed until the taskbar showed the wrong thing."""
    assert _load_icon_surface() is not None


def test_each_side_names_the_player_it_is():
    assert SATELLITE_PORTRAIT_TITLE == "Portrait AI Player"
    assert SATELLITE_LANDSCAPE_TITLE == "Landscape AI Player"


def test_the_dash_panels_carry_the_same_names_as_the_windows():
    """A panel and its window are the same player, so reading one name off the
    Dash and looking for it in Alt-Tab has to find it."""
    assert LABEL_PORTRAIT.replace("\n", " ") == SATELLITE_PORTRAIT_TITLE
    assert LABEL_LANDSCAPE.replace("\n", " ") == SATELLITE_LANDSCAPE_TITLE
