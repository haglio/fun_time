from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from .integration_support import FunTimeIntegrationSession, build_integration_config, integration_enabled


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not integration_enabled(),
    reason="Fun Time integration tests are opt-in and require a real Windows runtime",
)


@pytest.fixture
def integration_session(tmp_path):
    config_path = build_integration_config(tmp_path)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()


@pytest.mark.integration
def test_fun_time_startup_runtime_smoke(integration_session: FunTimeIntegrationSession):
    assert integration_session.controller_log.exists()
    assert integration_session.orchestrator_log.exists()


@pytest.mark.integration
def test_fun_time_portrait_lock_unlock_flow(integration_session: FunTimeIntegrationSession):
    integration_session.write_dashboard_command("portrait_lock")
    integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    integration_session.write_dashboard_command("portrait_lock")
    integration_session.wait_for_new_log("Unlocked portrait VLC", timeout=12)


@pytest.mark.integration
def test_fun_time_omnipause_toggle_flow(integration_session: FunTimeIntegrationSession):
    integration_session.write_dashboard_command("omnipause_toggle")
    integration_session.wait_for_new_log("OmniPause: entering", timeout=12)

    integration_session.write_dashboard_command("omnipause_toggle")
    integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)


@pytest.mark.integration
def test_fun_time_fmode_toggle_flow(integration_session: FunTimeIntegrationSession):
    integration_session.write_dashboard_command("fmode_toggle")
    integration_session.wait_for_new_log("F-mode hotkey: enabled", timeout=12)

    integration_session.write_dashboard_command("fmode_toggle")
    integration_session.wait_for_new_log("F-mode hotkey: disabled", timeout=12)


@pytest.mark.integration
def test_fun_time_robot_toggle_flow(integration_session: FunTimeIntegrationSession):
    assert integration_session.robot_hand_enabled_file.read_text(encoding="utf-8") == "1"
    integration_session.write_dashboard_command("robot_toggle")
    integration_session.wait_until(
        lambda: integration_session.robot_hand_enabled_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Robot Hand enabled file to flip off",
    )

    integration_session.write_dashboard_command("robot_toggle")
    integration_session.wait_until(
        lambda: integration_session.robot_hand_enabled_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Robot Hand enabled file to flip back on",
    )


@pytest.mark.integration
def test_fun_time_robot_hand_mode_file_flow(integration_session: FunTimeIntegrationSession):
    integration_session.write_robot_hand_mode(True)
    integration_session.wait_for_new_log("Entering Robot Hand mode", timeout=12)

    integration_session.write_robot_hand_mode(False)
    integration_session.wait_for_new_log("Leaving Robot Hand mode", timeout=12)


@pytest.mark.integration
def test_fun_time_portrait_trash_updates_temp_state(integration_session: FunTimeIntegrationSession):
    integration_session.write_dashboard_command("portrait_trash")
    chunk = integration_session.wait_for_new_log("Discarding from player 2:", timeout=12)
    match = re.search(r"Discarding from player 2:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded portrait path"
    trashed_path = Path(match.group(1).strip()).resolve()

    integration_session.wait_until(
        lambda: not integration_session.favs_contains(trashed_path),
        timeout=12,
        description="portrait sample to be removed from integration favs.csv",
    )
    integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in integration_session.weird_dir.iterdir()),
        timeout=12,
        description="portrait sample to be moved into the integration weird dir",
    )
