from __future__ import annotations

from unittest.mock import MagicMock, patch

import cv2

from fun_time.robot_hand.clipper.window_runtime import cleanup_window, ensure_window, window_closed


class FakeCv2Module:
    WINDOW_NORMAL = 0
    WND_PROP_VISIBLE = 1

    def __init__(self, *, visible: float = 1.0, property_error: Exception | None = None, waitkey_error_at: int | None = None):
        self.visible = visible
        self.property_error = property_error
        self.waitkey_error_at = waitkey_error_at
        self.named_windows: list[tuple[str, int]] = []
        self.resize_calls: list[tuple[str, int, int]] = []
        self.mouse_callbacks: list[tuple[str, object, object | None]] = []
        self.destroy_window_calls: list[str] = []
        self.destroy_all_calls = 0
        self.waitkey_calls: list[int] = []

    def namedWindow(self, name: str, mode: int) -> None:
        self.named_windows.append((name, mode))

    def resizeWindow(self, name: str, width: int, height: int) -> None:
        self.resize_calls.append((name, width, height))

    def setMouseCallback(self, name: str, callback, state=None) -> None:
        self.mouse_callbacks.append((name, callback, state))

    def getWindowProperty(self, _name: str, _prop: int) -> float:
        if self.property_error is not None:
            raise self.property_error
        return self.visible

    def destroyWindow(self, name: str) -> None:
        self.destroy_window_calls.append(name)

    def destroyAllWindows(self) -> None:
        self.destroy_all_calls += 1

    def waitKey(self, value: int) -> int:
        self.waitkey_calls.append(value)
        if self.waitkey_error_at is not None and len(self.waitkey_calls) >= self.waitkey_error_at:
            raise cv2.error("waitKey", "waitKey", "boom")
        return -1


def test_ensure_window_creates_window_sets_icon_and_mouse_callback():
    fake_cv2 = FakeCv2Module()
    state = object()
    callback = object()

    with patch("fun_time.robot_hand.clipper.window_runtime.set_cv2_window_icon") as set_icon:
        ensure_window("Clipper", state, mouse_callback=callback, cv2_module=fake_cv2)

    assert fake_cv2.named_windows == [("Clipper", fake_cv2.WINDOW_NORMAL)]
    assert fake_cv2.resize_calls == [("Clipper", 1520, 960)]
    assert fake_cv2.mouse_callbacks == [("Clipper", callback, state)]
    set_icon.assert_called_once_with("Clipper")


def test_window_closed_returns_true_when_window_is_hidden():
    fake_cv2 = FakeCv2Module(visible=0.0)

    assert window_closed("Clipper", cv2_module=fake_cv2) is True


def test_window_closed_returns_true_on_cv2_error():
    fake_cv2 = FakeCv2Module(property_error=cv2.error("getWindowProperty", "getWindowProperty", "boom"))

    assert window_closed("Clipper", cv2_module=fake_cv2) is True


def test_cleanup_window_terminates_export_releases_capture_and_drains_waitkeys():
    fake_cv2 = FakeCv2Module()
    state = MagicMock()
    state.cap = MagicMock()

    with patch("fun_time.robot_hand.clipper.window_runtime.terminate_export_subprocesses") as terminate_export:
        cleanup_window("Clipper", state, cv2_module=fake_cv2, sleep=lambda _seconds: None)

    terminate_export.assert_called_once_with(state)
    state.cap.release.assert_called_once_with()
    assert fake_cv2.mouse_callbacks[0][0] == "Clipper"
    assert fake_cv2.destroy_window_calls == ["Clipper"]
    assert fake_cv2.destroy_all_calls == 1
    assert fake_cv2.waitkey_calls == [1, 1, 1, 1, 1, 1]


def test_cleanup_window_stops_waitkey_drain_after_cv2_error():
    fake_cv2 = FakeCv2Module(waitkey_error_at=2)
    state = MagicMock()
    state.cap = MagicMock()

    with patch("fun_time.robot_hand.clipper.window_runtime.terminate_export_subprocesses"):
        cleanup_window("Clipper", state, cv2_module=fake_cv2, sleep=lambda _seconds: None)

    assert fake_cv2.waitkey_calls == [1, 1]
