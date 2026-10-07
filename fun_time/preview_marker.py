from __future__ import annotations

import os
from pathlib import Path

from shared_ui.palette import PREVIEW_INK, as_hex
from shared_ui.preview import Preview, preview_of, taskbar_identity, window_title
from shared_ui.preview_icon_pil import write_in_preview_ink

from fun_time.checkout_overrides import STATE_DIRNAME
from fun_time.cover_palette import WORDMARK_MAGENTA
from fun_time.project_paths import PROJECT_DIR
from fun_time.win32_taskbar import APP_USER_MODEL_ID

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
    if shown is None:
        return source
    inked = INKED_ICON_FOLDER / f"preview_{source.name}"
    if not inked.exists() or inked.stat().st_mtime < source.stat().st_mtime:
        inked.parent.mkdir(parents=True, exist_ok=True)
        write_in_preview_ink(source, inked)
    return inked


def wordmark_ink(shown: Preview | None) -> str:
    return WORDMARK_MAGENTA if shown is None else as_hex(PREVIEW_INK)
