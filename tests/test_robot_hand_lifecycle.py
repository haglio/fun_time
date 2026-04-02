from __future__ import annotations

import threading
import time

import pygame

from fun_time.robot_hand.lifecycle import RobotHandLifecycleController


class FakeView:
    pass


class FakeRenderer:
    def __init__(self):
        self.prepare_calls = 0

    def prepare_active_clip_for_current_size(self) -> None:
        self.prepare_calls += 1


class FakeSelection:
    def __init__(self):
        self.steps: list[int] = []

    def step(self, delta: int) -> None:
        self.steps.append(delta)


class FakeNotifier:
    def __init__(self):
        self.visible_updates: list[bool] = []
        self.closed = 0

    def notify_visible(self, value: bool) -> None:
        self.visible_updates.append(value)

    def close(self) -> None:
        self.closed += 1


def _build_controller():
    view = FakeView()
    renderer = FakeRenderer()
    selection = FakeSelection()
    notifier = FakeNotifier()
    stop_event = threading.Event()
    controller = RobotHandLifecycleController(
        view=view,
        renderer=renderer,
        selection=selection,
        stop_event=stop_event,
        notifier=notifier,
        resize_delay_ms=75,
    )
    return controller, view, renderer, selection, notifier, stop_event


def test_handle_key_steps_selection_on_bracket_keys():
    controller, _view, _renderer, selection, _notifier, _stop_event = _build_controller()

    controller._handle_key(type("Event", (), {"key": pygame.K_LEFTBRACKET})())
    controller._handle_key(type("Event", (), {"key": pygame.K_RIGHTBRACKET})())

    assert selection.steps == [-1, 1]


def test_resize_debounces_prepare_calls():
    controller, _view, renderer, _selection, _notifier, _stop_event = _build_controller()

    controller._on_resize()
    assert renderer.prepare_calls == 0

    controller._resize_pending_at = time.monotonic() - 0.2
    controller._flush_pending_resize()
    assert renderer.prepare_calls == 1


def test_on_close_stops_notifier():
    controller, _view, _renderer, _selection, notifier, stop_event = _build_controller()

    controller.on_close()

    assert stop_event.is_set()
    assert notifier.visible_updates == [False]
    assert notifier.closed == 1
