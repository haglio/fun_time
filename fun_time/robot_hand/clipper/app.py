from __future__ import annotations

import logging
import sys

from ...config import PROJECT_DIR
from ...logging_utils import configure_logging, install_exception_logging
from .launcher import messagebox, tk
from .session_launch import launch_state
from .ui import run_ui

CLIPPER_APP_USER_MODEL_ID = "FunTime.Clipper"


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        _ = set_app_id(CLIPPER_APP_USER_MODEL_ID)
    except Exception:
        pass


def _init_logger() -> logging.Logger:
    log_path = PROJECT_DIR / "state" / "clipper.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = configure_logging("clipper", log_path, console=False)
    install_exception_logging(logger)
    return logger


def main() -> int:
    _set_windows_app_user_model_id()
    logger = _init_logger()
    try:
        state = launch_state()
        run_ui(state)
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("Clipper crashed")
        if tk is not None:
            root = tk.Tk()
            root.withdraw()
            try:
                messagebox.showerror("Clipper", f"ERROR: {exc}")
            finally:
                root.destroy()
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1
