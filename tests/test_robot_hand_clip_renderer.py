from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fun_time.robot_hand.clip_renderer import ClipRenderController
from fun_time.robot_hand.clip_runtime import ClipCacheStore


class FakeImageLabel:
    def __init__(self):
        self.image = None
        self.configure_calls: list[object] = []

    def configure(self, *, image) -> None:
        self.configure_calls.append(image)
        self.image = image


class FakeScheduler:
    def __init__(self):
        self.calls: list[tuple[int, object]] = []

    def after(self, delay_ms: int, callback) -> None:
        self.calls.append((delay_ms, callback))


def _make_controller(*, viewport=(320, 240), render_batch=1):
    label = FakeImageLabel()
    scheduler = FakeScheduler()
    logger = MagicMock()
    clip_store = ClipCacheStore(limit=2)
    viewport_state = {"value": viewport}

    def make_photo(frame, width, height):
        return f"{frame}@{width}x{height}"

    controller = ClipRenderController(
        clip_store=clip_store,
        image_label=label,
        make_photo=make_photo,
        viewport_getter=lambda: viewport_state["value"],
        schedule_after=scheduler.after,
        render_batch=render_batch,
        logger=logger,
    )
    return controller, clip_store, label, scheduler, viewport_state, logger


def test_prepare_active_clip_for_current_size_sets_first_frame_and_schedules_render():
    controller, clip_store, label, scheduler, _viewport_state, _logger = _make_controller()
    path = Path("demo.mp4")
    clip_store.clip_cache[path] = {"pil_frames": ["f0", "f1"], "photo_frames": [], "photo_size": None}
    controller.set_current_clip_path(path)

    controller.prepare_active_clip_for_current_size()

    entry = clip_store.clip_cache[path]
    assert entry["photo_size"] == (320, 240)
    assert entry["photo_frames"][0] == "f0@320x240"
    assert controller.current_frame_index == 0
    assert label.image == "f0@320x240"
    assert scheduler.calls == [(1, controller.render_step)]


def test_render_step_processes_batch_and_reschedules_when_more_frames_remain():
    controller, clip_store, label, scheduler, _viewport_state, _logger = _make_controller(render_batch=1)
    path = Path("demo.mp4")
    clip_store.clip_cache[path] = {
        "pil_frames": ["f0", "f1", "f2"],
        "photo_frames": ["f0@320x240", None, None],
        "photo_size": (320, 240),
    }
    controller.set_current_clip_path(path)
    controller.current_frame_index = 1
    controller.render_queue.extend([1, 2])

    controller.render_step()

    entry = clip_store.clip_cache[path]
    assert entry["photo_frames"][1] == "f1@320x240"
    assert label.image == "f1@320x240"
    assert list(controller.render_queue) == [2]
    assert scheduler.calls == [(1, controller.render_step)]


def test_display_frame_rebuilds_cache_for_new_viewport_size():
    controller, clip_store, label, scheduler, viewport_state, _logger = _make_controller()
    path = Path("demo.mp4")
    clip_store.clip_cache[path] = {"pil_frames": ["f0", "f1"], "photo_frames": [], "photo_size": None}
    controller.set_current_clip_path(path)
    controller.prepare_active_clip_for_current_size()
    scheduler.calls.clear()
    controller.render_scheduled = False
    viewport_state["value"] = (640, 480)

    shown = controller.display_frame(1)

    entry = clip_store.clip_cache[path]
    assert shown is True
    assert entry["photo_size"] == (640, 480)
    assert entry["photo_frames"][1] == "f1@640x480"
    assert controller.current_frame_index == 1
    assert label.image == "f1@640x480"
    assert scheduler.calls == [(1, controller.render_step)]


def test_display_frame_returns_false_when_no_active_clip_is_loaded():
    controller, _clip_store, _label, _scheduler, _viewport_state, _logger = _make_controller()

    assert controller.display_frame(0) is False
