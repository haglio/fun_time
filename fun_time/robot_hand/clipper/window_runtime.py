from __future__ import annotations

import time

import cv2

from .export import terminate_export_subprocesses
from .window_icons import set_cv2_window_icon


def ensure_window(window_name: str, state, *, mouse_callback, cv2_module=cv2) -> None:
    cv2_module.namedWindow(window_name, cv2_module.WINDOW_NORMAL)
    cv2_module.resizeWindow(window_name, 1520, 960)
    set_cv2_window_icon(window_name)
    cv2_module.setMouseCallback(window_name, mouse_callback, state)


def window_closed(window_name: str, *, cv2_module=cv2) -> bool:
    try:
        return cv2_module.getWindowProperty(window_name, cv2_module.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def cleanup_window(window_name: str, state, *, cv2_module=cv2, sleep=time.sleep) -> None:
    terminate_export_subprocesses(state)
    state.cap.release()
    try:
        cv2_module.setMouseCallback(window_name, lambda *args: None)
    except Exception:
        pass
    try:
        cv2_module.destroyWindow(window_name)
    except cv2.error:
        pass
    try:
        cv2_module.destroyAllWindows()
    except cv2.error:
        pass
    for _ in range(6):
        try:
            cv2_module.waitKey(1)
        except cv2.error:
            break
        sleep(0.01)
