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


def test_pygame_view_create(mock_pygame):
    from fun_time.robot_hand.pygame_view import PygameView

    view = PygameView(width=800, height=600, x=100, y=50, title="Robot Hand")

    assert view.width == 800
    assert view.height == 600
