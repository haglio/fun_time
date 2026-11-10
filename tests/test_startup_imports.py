from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
EXAMPLE_CONTENT = PROJECT_DIR / "content.example.json"

_THE_MODULE_LAUNCH_VBS_RUNS = "fun_time.orchestrator"
_SESSION_MACHINERY_LOADED_UNDER_THE_LOADING_SCREEN = "fun_time.windows_bridge_orchestrator"
_READS_THE_OVERLAY_AS_IT_IMPORTS = "fun_time.voice_commands"

_STARTUP_MODULES = (
    _THE_MODULE_LAUNCH_VBS_RUNS,
    _SESSION_MACHINERY_LOADED_UNDER_THE_LOADING_SCREEN,
    _READS_THE_OVERLAY_AS_IT_IMPORTS,
)

_SECONDS_OF_IMPORT_ON_A_COLD_MACHINE = (
    _SESSION_MACHINERY_LOADED_UNDER_THE_LOADING_SCREEN,
    "numpy",
    "cv2",
    "PyQt6.QtWidgets",
    "tkinter",
)

_IMPORT_TIME_OVERLAY_KEYS = ("clip_jump_phrases", "flick_flip_phrases")


def _import_startup_graph(content_overlay: Path) -> subprocess.CompletedProcess:
    imports = "; ".join(f"import {name}" for name in _STARTUP_MODULES)
    driver = (
        "import sys; from pathlib import Path; overlay = Path(sys.argv[1]); "
        "import fun_time.content as c; _orig = c.load_content; "
        "c.load_content = lambda *a, **k: _orig(overlay, overlay); "
        + imports
    )
    return subprocess.run(
        [sys.executable, "-c", driver, str(content_overlay)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_checkout_with_only_the_example_overlay_imports_all_a_launch_does():
    result = _import_startup_graph(EXAMPLE_CONTENT)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("required_key", _IMPORT_TIME_OVERLAY_KEYS)
def test_a_missing_required_overlay_key_fails_the_import(tmp_path, required_key):
    data = json.loads(EXAMPLE_CONTENT.read_text(encoding="utf-8"))
    del data[required_key]
    broken_overlay = tmp_path / "content.json"
    broken_overlay.write_text(json.dumps(data), encoding="utf-8")

    result = _import_startup_graph(broken_overlay)
    assert result.returncode != 0
    assert required_key in result.stderr


def test_the_launch_loads_nothing_the_loading_screen_does_not_need():
    result = subprocess.run(
        [sys.executable, "-c",
         (f"import {_THE_MODULE_LAUNCH_VBS_RUNS}, sys;"
         f"print([m for m in {_SECONDS_OF_IMPORT_ON_A_COLD_MACHINE!r} if m in sys.modules])")],
        cwd=PROJECT_DIR, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"the launch loads {result.stdout.strip()} over a bare desktop, "
        "before it can raise the loading screen"
    )


@pytest.mark.parametrize("package", ["fun_time", "fun_time_vr"])
def test_the_sys_path_override_is_applied_before_the_package_imports_anything_else(package):
    tree = ast.parse((PROJECT_DIR / package / "__init__.py").read_text(encoding="utf-8"))
    statements = [node for node in tree.body
                  if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    applies = next(index for index, node in enumerate(statements)
                   if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and getattr(node.value.func, "id", "") == "apply_sibling_checkouts_to_sys_path")
    imports_above_it = [
        node.module for node in statements[:applies]
        if isinstance(node, ast.ImportFrom) and not node.module.endswith("project_paths")
        and node.module != "__future__"
    ]

    assert imports_above_it == []
