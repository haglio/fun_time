from __future__ import annotations

import logging
import os
from pathlib import Path

from app_support.win32 import described_taskbar_app, dress_window
from shared_ui.palette import LOADING_ACCENT, PREVIEW_INK, Rgb
from shared_ui.preview import Preview, preview_of, taskbar_identity, window_title
from shared_ui.preview_icon_pil import icon_file as inked_icon_file

from fun_time.checkout_overrides import STATE_DIRNAME
from fun_time.project_paths import PROJECT_DIR
from fun_time.win32_taskbar import APP_USER_MODEL_ID

logger = logging.getLogger(__name__)

FLAG = "FUN_TIME_BRANCH_SESSION"
APP_TITLE = "Fun Time"
INKED_ICON_FOLDER = PROJECT_DIR / STATE_DIRNAME


def shown_as() -> Preview | None:
    return preview_of(PROJECT_DIR) if os.environ.get(FLAG) == "1" else None


def app_title(shown: Preview | None) -> str:
    return window_title(APP_TITLE, shown)


def session_identity(shown: Preview | None) -> str:
    return taskbar_identity(APP_USER_MODEL_ID, shown)


def icon_file(source: Path, shown: Preview | None) -> Path:
    return inked_icon_file(source, shown, INKED_ICON_FOLDER)


def dress_the_window(hwnd: int) -> None:
    identity = session_identity(shown_as())
    try:
        dress_window(hwnd, identity, described_taskbar_app(identity))
    except OSError:
        logger.info("The taskbar keeps its own idea of window %s", hwnd, exc_info=True)


def wordmark_ink(shown: Preview | None) -> Rgb:
    return LOADING_ACCENT if shown is None else PREVIEW_INK
