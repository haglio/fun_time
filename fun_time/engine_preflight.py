from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

_PROBE = "from player_core.mpv_player import _import_mpv; _import_mpv()"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def engine_preflight_error(
    interpreters: Mapping[str, Path], *, run: Runner = subprocess.run
) -> str | None:
    for name, exe in interpreters.items():
        result = run(
            [str(exe), "-c", _PROBE],
            capture_output=True,
            text=True,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            return (
                f"Fun Time can't start {name}: its video engine (libmpv) failed to "
                f"load, so its window would open empty.\n\n"
                f"Fetch the engine once with:\n"
                f"    python tools/fetch_libmpv.py\n\n"
                f"(interpreter: {exe})"
            )
    return None


def player_interpreters(paths) -> dict[str, Path]:
    interpreters: dict[str, Path] = {}
    if paths.genau_python_exe:
        interpreters["the main player"] = Path(paths.genau_python_exe)
    interpreters["the side players"] = Path(paths.python_exe)
    return interpreters


def show_engine_alert(text: str) -> None:
    from shared_ui.alert import Level, show_alert

    from fun_time.project_paths import PROJECT_ICON

    show_alert("Fun Time", text, level=Level.ERROR, icon=PROJECT_ICON)


def engine_missing_abort(
    config,
    *,
    run: Runner = subprocess.run,
    alert: Callable[[str], None] = show_engine_alert,
    log: Callable[[str], None] | None = None,
) -> bool:
    error = engine_preflight_error(player_interpreters(config.paths), run=run)
    if error is None:
        return False
    if log is not None:
        log(error)
    alert(error)
    return True
