from __future__ import annotations

from fun_time.robot_hand.status_overlay import StatusOverlayController


def test_show_sets_visible():
    controller = StatusOverlayController(hide_delay_ms=100, can_hide=lambda: True)

    controller.show()

    assert controller.visible is True


def test_hide_only_hides_when_allowed():
    controller = StatusOverlayController(hide_delay_ms=100, can_hide=lambda: False)
    controller.show()

    controller.hide()

    assert controller.visible is True


def test_hide_clears_visible_when_allowed():
    controller = StatusOverlayController(hide_delay_ms=100, can_hide=lambda: True)
    controller.show()

    controller.hide()

    assert controller.visible is False


def test_schedule_hide_hides_after_delay():
    now = [0.0]
    controller = StatusOverlayController(
        hide_delay_ms=250,
        can_hide=lambda: True,
        now_source=lambda: now[0],
    )
    controller.show()

    controller.schedule_hide()
    now[0] = 0.1
    assert controller.visible is True

    now[0] = 0.3
    assert controller.visible is False


def test_on_mouse_motion_shows_and_schedules_hide():
    now = [0.0]
    controller = StatusOverlayController(
        hide_delay_ms=250,
        can_hide=lambda: True,
        now_source=lambda: now[0],
    )

    controller.on_mouse_motion()

    assert controller.visible is True
    now[0] = 0.3
    assert controller.visible is False


def test_on_mouse_leave_hides_when_allowed():
    controller = StatusOverlayController(hide_delay_ms=250, can_hide=lambda: True)
    controller.show()

    controller.on_mouse_leave()

    assert controller.visible is False
