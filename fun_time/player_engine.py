from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time import preview_marker
from fun_time.checkout_overrides import genau_project_kwargs
from fun_time.project_paths import PROJECT_ICON

ENGINE_DLL = "libmpv-2.dll"

_IMPORT_THE_ENGINE = "from player_core.mpv_player import _import_mpv; _import_mpv()"
_WHERE_IT_LOOKS_FIRST = (
    "from player_core.libmpv_loader import libmpv_dirs; print(libmpv_dirs()[0])")
_WHERE_THE_SHARED_COPY_IS = (
    "from player_core.libmpv_loader import machine_libmpv_dir; print(machine_libmpv_dir())")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def player_interpreters(paths) -> dict[str, Path]:
    interpreters: dict[str, Path] = {}
    if paths.genau_python_exe:
        interpreters["the main player"] = Path(paths.genau_python_exe)
    interpreters["the side players"] = Path(paths.python_exe)
    return interpreters


def _ask(exe, code: str, project_path: str, *, run: Runner):
    return run(
        [str(exe), "-c", code],
        capture_output=True,
        text=True,
        **genau_project_kwargs(project_path),
        **hidden_subprocess_kwargs(),
    )


def _one_path(result) -> Path | None:
    lines = result.stdout.strip().splitlines() if result.returncode == 0 else []
    return Path(lines[0]) if len(lines) == 1 else None


def _shared_engine_copy(python_exe, *, run: Runner) -> Path | None:
    found = _one_path(_ask(python_exe, _WHERE_THE_SHARED_COPY_IS, "", run=run))
    return found / ENGINE_DLL if found else None


def _vendor_the_engine(exe, project_path: str, *, shared: Path | None, run: Runner) -> None:
    if shared is None or not shared.is_file():
        return
    looks_in = _one_path(_ask(exe, _WHERE_IT_LOOKS_FIRST, project_path, run=run))
    if looks_in is None or (looks_in / ENGINE_DLL).is_file():
        return
    looks_in.mkdir(parents=True, exist_ok=True)
    shutil.copy2(shared, looks_in / ENGINE_DLL)


def _why(stderr: str) -> str:
    return "\n".join(line for line in stderr.strip().splitlines()
                     if line.strip() and not line.startswith((" ", "\t", "Traceback")))


def _refusal(name: str, exe, said: str) -> str:
    return (
        f"Fun Time can't start {name}: its video engine (libmpv) failed to load, so "
        f"its window would open empty.\n\n"
        f"{said}\n\n"
        f"Fetch the engine once with:\n"
        f"    python tools/fetch_libmpv.py\n\n"
        f"(interpreter: {exe})"
    )


def engine_error(config, *, run: Runner = subprocess.run) -> str | None:
    project_path = config.paths.genau_project_path
    for name, exe in player_interpreters(config.paths).items():
        if _ask(exe, _IMPORT_THE_ENGINE, project_path, run=run).returncode == 0:
            continue
        _vendor_the_engine(exe, project_path, run=run,
                           shared=_shared_engine_copy(config.paths.python_exe, run=run))
        result = _ask(exe, _IMPORT_THE_ENGINE, project_path, run=run)
        if result.returncode != 0:
            return _refusal(name, exe, _why(result.stderr))
    return None


def show_engine_alert(text: str) -> None:
    # Qt loads only for this: the check itself runs before any window.
    from shared_ui.alert import Level, show_alert  # noqa: PLC0415

    shown = preview_marker.shown_as()
    show_alert(preview_marker.app_title(shown), text, level=Level.ERROR,
               icon=preview_marker.icon_file(PROJECT_ICON, shown))


def engine_missing_abort(
    config,
    *,
    run: Runner = subprocess.run,
    alert: Callable[[str], None] = show_engine_alert,
    log: Callable[[str], None] | None = None,
    uncover: Callable[[], None] | None = None,
) -> bool:
    error = engine_error(config, run=run)
    if error is None:
        return False
    if log is not None:
        log(error)
    if uncover is not None:
        uncover()
    alert(error)
    return True
