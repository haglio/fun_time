from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def mock_pygame():
    with patch.dict("sys.modules", {
        "pygame": MagicMock(),
        "pygame._sdl2": MagicMock(),
        "pygame._sdl2.video": MagicMock(),
    }) as mocked:
        yield mocked["pygame"]


def test_pygame_view_creates_window_and_renderer(mock_pygame):
    from fun_time.robot_hand.pygame_view import PygameView

    view = PygameView(width=1200, height=900, x=10, y=20, title="Robot Hand")

    assert view.width == 1200
    assert view.height == 900
    mock_pygame.init.assert_called_once()


def test_pygame_view_get_size_delegates_to_window(mock_pygame):
    from fun_time.robot_hand.pygame_view import PygameView

    view = PygameView(width=800, height=600)
    view.window.size = (800, 600)

    assert view.get_size() == (800, 600)
