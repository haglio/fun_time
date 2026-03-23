from __future__ import annotations

from fun_time.robot_hand.status_overlay import StatusOverlayController


class FakeRoot:
    def __init__(self):
        self.after_calls: list[tuple[int, object]] = []
        self.after_cancel_calls: list[object] = []

    def after(self, delay_ms: int, callback):
        token = f"after-{len(self.after_calls) + 1}"
        self.after_calls.append((delay_ms, callback))
        return token

    def after_cancel(self, token) -> None:
        self.after_cancel_calls.append(token)


class FakeLabel:
    def __init__(self):
        self.placed = False
        self.place_calls = 0
        self.place_forget_calls = 0

    def place(self, **_kwargs) -> None:
        self.placed = True
        self.place_calls += 1

    def place_forget(self) -> None:
        self.placed = False
        self.place_forget_calls += 1


def test_show_places_label():
    label = FakeLabel()
    controller = StatusOverlayController(root=FakeRoot(), label=label, hide_delay_ms=100, can_hide=lambda: True)

    controller.show()

    assert label.placed is True
    assert label.place_calls == 1


def test_hide_only_hides_when_allowed():
    label = FakeLabel()
    controller = StatusOverlayController(root=FakeRoot(), label=label, hide_delay_ms=100, can_hide=lambda: False)
    controller.show()

    controller.hide()

    assert label.placed is True
    assert label.place_forget_calls == 0


def test_schedule_hide_cancels_previous_timer():
    label = FakeLabel()
    root = FakeRoot()
    controller = StatusOverlayController(root=root, label=label, hide_delay_ms=250, can_hide=lambda: True)

    controller.schedule_hide()
    controller.schedule_hide()

    assert [delay for delay, _callback in root.after_calls] == [250, 250]
    assert root.after_cancel_calls == ["after-1"]


def test_on_mouse_motion_shows_and_schedules_hide():
    label = FakeLabel()
    root = FakeRoot()
    controller = StatusOverlayController(root=root, label=label, hide_delay_ms=250, can_hide=lambda: True)

    controller.on_mouse_motion()

    assert label.placed is True
    assert len(root.after_calls) == 1


def test_on_mouse_leave_hides_when_allowed():
    label = FakeLabel()
    root = FakeRoot()
    controller = StatusOverlayController(root=root, label=label, hide_delay_ms=250, can_hide=lambda: True)
    controller.show()

    controller.on_mouse_leave()

    assert label.placed is False
    assert label.place_forget_calls == 1
