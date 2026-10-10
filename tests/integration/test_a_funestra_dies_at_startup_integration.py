from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Fun Time integration tests require Windows",
)

WHAT_IT_SAID = "a clip folder that dies importing, on purpose"

_THE_REAL_PACKAGE_UNDER_IT = """import os
import sys

_where_the_real_modules_could_be = (os.path.join(entry or os.getcwd(), "funestra_core")
                                    for entry in sys.path)
__path__.append(next(real for real in _where_the_real_modules_could_be
                     if os.path.isfile(os.path.join(real, "mpv_engine.py"))))
"""


def _a_funestra_core_whose_flick_folder_dies_importing(root: Path) -> Path:
    """A checkout whose funestra_core is the real one under a flick_folder that
    raises on import.  The Main Funestra imports that module as Genau's engine
    comes up; the satellites never do, and the room's check of the video engine
    reads the real loader -- so the one death is the Main Funestra's."""
    package = root / "a_checkout_whose_flick_folder_dies_importing" / "funestra_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(_THE_REAL_PACKAGE_UNDER_IT, encoding="utf-8")
    (package / "flick_folder.py").write_text(
        f"raise ImportError({WHAT_IT_SAID!r})\n", encoding="utf-8")
    return package.parent


def _run_the_funestras_out_of(config_path: Path, checkout: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    paths = config["paths"]
    paths["genau_project_dirs"] = [str(checkout), *paths.get("genau_project_dirs", [])]
    config_path.write_text(json.dumps(config), encoding="utf-8")


def test_a_funestra_that_dies_importing_stops_the_startup_saying_what_it_said():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    _run_the_funestras_out_of(config_path, _a_funestra_core_whose_flick_folder_dies_importing(temp_root))
    session = FunTimeIntegrationSession(config_path)
    try:
        logged = session.start_one_that_stops_starting_up(
            env_overrides={"FUN_TIME_INTEGRATION_OVERLAYS": "1"})
    finally:
        session.stop()

    assert "the Main Funestra closed itself before the room was up" in logged
    assert WHAT_IT_SAID in logged
