from __future__ import annotations

import threading

from fun_time.robot_hand.lifecycle import RobotHandLifecycleController


class FakeRoot:
    def __init__(self):
        self.bindings: dict[str, object] = {}
        self.protocols: dict[str, object] = {}
        self.after_calls: list[tuple[int, object]] = []
        self.after_cancel_calls: list[object] = []
        self.destroy_calls = 0

    def bind(self, event: str, callback) -> None:
        self.bindings[event] = callback

    def protocol(self, name: str, callback) -> None:
        self.protocols[name] = callback

    def after(self, delay: int, callback):
        token = f"after-{len(self.after_calls) + 1}"
        self.after_calls.append((delay, callback))
        return token

    def after_cancel(self, token) -> None:
        self.after_cancel_calls.append(token)

    def destroy(self) -> None:
        self.destroy_calls += 1


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


class FakeStatusOverlay:
    def __init__(self):
        self.motion_calls = 0
        self.leave_calls = 0

    def on_mouse_motion(self, _event=None) -> None:
        self.motion_calls += 1

    def on_mouse_leave(self, _event=None) -> None:
        self.leave_calls += 1


class FakeNotifier:
    def __init__(self):
        self.visible_updates: list[bool] = []
        self.closed = 0

    def notify_visible(self, value: bool) -> None:
        self.visible_updates.append(value)

    def close(self) -> None:
        self.closed += 1


def _build_controller():
    root = FakeRoot()
    renderer = FakeRenderer()
    selection = FakeSelection()
    status_overlay = FakeStatusOverlay()
    notifier = FakeNotifier()
    stop_event = threading.Event()
    controller = RobotHandLifecycleController(
        root=root,
        renderer=renderer,
        selection=selection,
        status_overlay=status_overlay,
        stop_event=stop_event,
        notifier=notifier,
        resize_delay_ms=75,
    )
    return controller, root, renderer, selection, status_overlay, notifier, stop_event


def test_bind_root_events_registers_expected_callbacks():
    controller, root, _renderer, _selection, _status_overlay, _notifier, _stop_event = _build_controller()

    controller.bind_root_events()

    assert set(root.bindings) == {"<Motion>", "<Leave>", "<Configure>", "[", "]"}
    assert set(root.protocols) == {"WM_DELETE_WINDOW"}


def test_bound_overlay_callbacks_delegate_to_status_overlay():
    controller, root, _renderer, _selection, status_overlay, _notifier, _stop_event = _build_controller()
    controller.bind_root_events()

    root.bindings["<Motion>"]()
    root.bindings["<Leave>"]()

    assert status_overlay.motion_calls == 1
    assert status_overlay.leave_calls == 1


def test_bound_key_callbacks_step_selection():
    controller, root, _renderer, selection, _status_overlay, _notifier, _stop_event = _build_controller()
    controller.bind_root_events()

    root.bindings["["](None)
    root.bindings["]"](None)

    assert selection.steps == [-1, 1]


def test_on_resize_debounces_prepare_calls():
    controller, root, renderer, _selection, _status_overlay, _notifier, _stop_event = _build_controller()

    controller.on_resize()
    controller.on_resize()

    assert root.after_calls == [
        (75, renderer.prepare_active_clip_for_current_size),
        (75, renderer.prepare_active_clip_for_current_size),
    ]
    assert root.after_cancel_calls == ["after-1"]


def test_on_close_stops_notifier_and_destroys_root():
    controller, root, _renderer, _selection, _status_overlay, notifier, stop_event = _build_controller()

    controller.on_close()

    assert stop_event.is_set()
    assert notifier.visible_updates == [False]
    assert notifier.closed == 1
    assert root.destroy_calls == 1
