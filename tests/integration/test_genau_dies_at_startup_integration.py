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


def _a_genau_checkout_that_dies_importing(root: Path) -> Path:
    package = root / "a_genau_checkout_that_dies_importing" / "genau"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "from player_core.clip_folder import a_name_player_core_never_had\n", encoding="utf-8")
    return package.parent


def _run_genau_out_of(config_path: Path, checkout: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    paths = config["paths"]
    paths["genau_project_dirs"] = [str(checkout), *paths.get("genau_project_dirs", [])]
    config_path.write_text(json.dumps(config), encoding="utf-8")


def test_a_genau_that_dies_importing_stops_the_startup_saying_what_it_said():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    _run_genau_out_of(config_path, _a_genau_checkout_that_dies_importing(temp_root))
    session = FunTimeIntegrationSession(config_path)
    try:
        logged = session.start_one_that_stops_starting_up(
            env_overrides={"FUN_TIME_INTEGRATION_OVERLAYS": "1"})
    finally:
        session.stop()

    assert "Genau closed itself before the room was up" in logged
    assert ("cannot import name 'a_name_player_core_never_had' from 'player_core.clip_folder'"
            in logged)
