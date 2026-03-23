from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .paths import MODULE_DIR


def clipper_icon_path() -> Path:
    return MODULE_DIR / "clipper.ico"


def set_tk_window_icon(root: Any) -> None:
    icon_path = clipper_icon_path()
    if not icon_path.exists():
        return
    try:
        root.iconbitmap(str(icon_path))
    except Exception:
        pass


def set_cv2_window_icon(window_name: str) -> None:
    if sys.platform != "win32":
        return
    icon_path = clipper_icon_path()
    if not icon_path.exists():
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        image_icon = 1
        load_from_file = 0x10
        wm_seticon = 0x80
        icon_small = 0
        icon_big = 1
        hwnd = user32.FindWindowW(None, window_name)
        if not hwnd:
            return
        hicon = user32.LoadImageW(None, str(icon_path), image_icon, 0, 0, load_from_file)
        if not hicon:
            return
        user32.SendMessageW(hwnd, wm_seticon, icon_small, hicon)
        user32.SendMessageW(hwnd, wm_seticon, icon_big, hicon)
    except Exception:
        pass
