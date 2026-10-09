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

WHAT_IT_SAID = "a player_core that dies importing, on purpose"


def _a_player_core_that_dies_importing(root: Path) -> Path:
    """A checkout whose player_core raises on import: every player this session
    launches imports it ahead of the venv's, so each dies as it starts."""
    package = root / "a_checkout_whose_player_core_dies_importing" / "player_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f"raise ImportError({WHAT_IT_SAID!r})\n", encoding="utf-8")
    return package.parent


def _run_the_players_out_of(config_path: Path, checkout: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    paths = config["paths"]
    paths["genau_project_dirs"] = [str(checkout), *paths.get("genau_project_dirs", [])]
    config_path.write_text(json.dumps(config), encoding="utf-8")


def test_a_player_that_dies_importing_stops_the_startup_saying_what_it_said():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    _run_the_players_out_of(config_path, _a_player_core_that_dies_importing(temp_root))
    session = FunTimeIntegrationSession(config_path)
    try:
        logged = session.start_one_that_stops_starting_up(
            env_overrides={"FUN_TIME_INTEGRATION_OVERLAYS": "1"})
    finally:
        session.stop()

    assert "closed itself before the room was up" in logged
    assert WHAT_IT_SAID in logged
