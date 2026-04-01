from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fun_time.robot_hand.clip_renderer import ClipRenderController
from fun_time.robot_hand.clip_runtime import ClipCacheStore


def _make_controller():
    logger = MagicMock()
    clip_store = ClipCacheStore(limit=2)
    display_calls: list[object] = []

    controller = ClipRenderController(
        clip_store=clip_store,
        display_frame_fn=display_calls.append,
        logger=logger,
    )
    return controller, clip_store, display_calls, logger


def test_prepare_active_clip_displays_first_frame():
    controller, clip_store, display_calls, _logger = _make_controller()
    path = Path("demo.mp4")
    clip_store.clip_cache[path] = {"frames": ["f0", "f1"]}
    controller.set_current_clip_path(path)

    controller.prepare_active_clip_for_current_size()

    assert controller.current_frame_index == 0
    assert display_calls == ["f0"]


def test_display_frame_sends_frame_to_display_fn():
    controller, clip_store, display_calls, _logger = _make_controller()
    path = Path("demo.mp4")
    clip_store.clip_cache[path] = {"frames": ["f0", "f1", "f2"]}
    controller.set_current_clip_path(path)

    shown = controller.display_frame(1)

    assert shown is True
    assert controller.current_frame_index == 1
    assert display_calls == ["f1"]


def test_display_frame_skips_when_index_unchanged():
    controller, clip_store, display_calls, _logger = _make_controller()
    path = Path("demo.mp4")
    clip_store.clip_cache[path] = {"frames": ["f0", "f1"]}
    controller.set_current_clip_path(path)

    controller.display_frame(0)
    controller.display_frame(0)

    assert display_calls == ["f0"]


def test_display_frame_returns_false_when_no_active_clip_is_loaded():
    controller, _clip_store, display_calls, _logger = _make_controller()

    assert controller.display_frame(0) is False
    assert display_calls == []
