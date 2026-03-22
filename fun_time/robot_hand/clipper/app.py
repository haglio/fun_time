from __future__ import annotations

import logging
import sys
from pathlib import Path

from ...config import PROJECT_DIR
from ...logging_utils import configure_logging, install_exception_logging
from .paths import LAST_SESSION_FILE, ensure_runtime_dirs
from .state import VideoState, make_video_state
from .ui import launcher_dialog, messagebox, run_ui, tk
from .utils import parse_timestamp, read_json

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


def launch_state() -> VideoState:
    ensure_runtime_dirs()
    info = launcher_dialog()
    if not info.get("ok"):
        raise SystemExit(0)
    if info["mode"] == "load":
        payload = read_json(Path(info["session_json"]))
        state = make_video_state(
            payload["video_path"],
            payload.get("session_name", Path(info["session_json"]).stem),
            0.0,
            5.0,
            payload_override=payload,
        )
        state.session_path = str(Path(info["session_json"]))
        state.original_session_payload = dict(payload)
        LAST_SESSION_FILE.write_text(state.session_path, encoding="utf-8")
        return state
    state = make_video_state(info["video_file"], info["session_name"], parse_timestamp(info["timestamp"]), info["seconds"])
    state.autosave_session()
    state.original_session_payload = dict(state.current_payload())
    return state


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
                messagebox.showerror("Frame Loop Trimmer", f"ERROR: {exc}")
            finally:
                root.destroy()
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1