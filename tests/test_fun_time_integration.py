from __future__ import annotations

import re
import shutil
import sys
import time
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


@pytest.fixture(scope="module")
def shared_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


@pytest.fixture
def isolated_integration_session():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    session = FunTimeIntegrationSession(config_path)
    try:
        session.start()
        yield session
    finally:
        session.stop()
        shutil.rmtree(temp_root, ignore_errors=True)


@pytest.mark.integration
def test_fun_time_startup_runtime_smoke(shared_integration_session: FunTimeIntegrationSession):
    assert shared_integration_session.windows_bridge_log.exists()
    assert shared_integration_session.orchestrator_log.exists()


@pytest.mark.integration
def test_fun_time_portrait_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait VLC", timeout=12)


@pytest.mark.integration
def test_fun_time_omnipause_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering", timeout=12)

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)


@pytest.mark.integration
def test_fun_time_fmode_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode hotkey: enabled", timeout=12)

    shared_integration_session.write_dashboard_command("fmode_toggle")
    shared_integration_session.wait_for_new_log("F-mode hotkey: disabled", timeout=12)


@pytest.mark.integration
def test_fun_time_robot_toggle_flow(shared_integration_session: FunTimeIntegrationSession):
    assert shared_integration_session.robot_hand_enabled_file.read_text(encoding="utf-8") == "1"
    shared_integration_session.write_dashboard_command("robot_toggle")
    shared_integration_session.wait_until(
        lambda: shared_integration_session.robot_hand_enabled_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Robot Hand enabled file to flip off",
    )

    shared_integration_session.write_dashboard_command("robot_toggle")
    shared_integration_session.wait_until(
        lambda: shared_integration_session.robot_hand_enabled_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Robot Hand enabled file to flip back on",
    )


@pytest.mark.integration
def test_fun_time_robot_hand_mode_file_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_robot_hand_mode(True)
    shared_integration_session.wait_for_new_log("Entering Robot Hand mode", timeout=12)

    shared_integration_session.write_robot_hand_mode(False)
    shared_integration_session.wait_for_new_log("Leaving Robot Hand mode", timeout=12)


@pytest.mark.integration
def test_fun_time_robot_hand_active_playback(shared_integration_session: FunTimeIntegrationSession):
    """Entering Robot Hand mode with link enabled must unpause Robot Hand and audio."""
    s = shared_integration_session
    assert s.robot_hand_enabled_file.read_text(encoding="utf-8") == "1"

    s.write_robot_hand_mode(True)
    s.wait_for_new_log("Entering Robot Hand mode", timeout=12)

    s.wait_until(
        lambda: s.config.robot_hand_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Robot Hand paused file to flip off (active playback)",
    )
    s.wait_until(
        lambda: s.config.audio_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Audio paused file to flip off (audio companion active)",
    )

    s.write_robot_hand_mode(False)
    s.wait_for_new_log("Leaving Robot Hand mode", timeout=12)

    s.wait_until(
        lambda: s.config.robot_hand_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Robot Hand paused file to flip back on after leaving mode",
    )
    s.wait_until(
        lambda: s.config.audio_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Audio paused file to flip back on after leaving mode",
    )


@pytest.mark.integration
def test_fun_time_landscape_lock_unlock_flow(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape VLC", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape VLC", timeout=12)


@pytest.mark.integration
def test_fun_time_portrait_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Locked portrait VLC", timeout=12)

    shared_integration_session.write_dashboard_command("portrait_lock")
    shared_integration_session.wait_for_new_log("Unlocked portrait VLC", timeout=12)


@pytest.mark.integration
def test_fun_time_landscape_next_cancels_lock(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape VLC", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_next")
    time.sleep(1.5)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Locked landscape VLC", timeout=12)

    shared_integration_session.write_dashboard_command("landscape_lock")
    shared_integration_session.wait_for_new_log("Unlocked landscape VLC", timeout=12)


@pytest.mark.integration
def test_fun_time_omnipause_while_robot_hand_mode(shared_integration_session: FunTimeIntegrationSession):
    shared_integration_session.write_robot_hand_mode(True)
    shared_integration_session.wait_for_new_log("Entering Robot Hand mode", timeout=12)

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: entering", timeout=12)
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.robot_hand_paused_file.read_text(encoding="utf-8") == "1",
        timeout=12,
        description="Robot Hand paused file to flip on",
    )

    shared_integration_session.write_dashboard_command("omnipause_toggle")
    shared_integration_session.wait_for_new_log("OmniPause: leaving", timeout=12)
    shared_integration_session.wait_until(
        lambda: shared_integration_session.config.robot_hand_paused_file.read_text(encoding="utf-8") == "0",
        timeout=12,
        description="Robot Hand paused file to flip off",
    )

    shared_integration_session.write_robot_hand_mode(False)
    shared_integration_session.wait_for_new_log("Leaving Robot Hand mode", timeout=12)


@pytest.mark.integration
def test_fun_time_landscape_trash_updates_temp_state(isolated_integration_session: FunTimeIntegrationSession):
    isolated_integration_session.write_dashboard_command("landscape_trash")
    chunk = isolated_integration_session.wait_for_new_log("Discarding from player 3:", timeout=12)
    match = re.search(r"Discarding from player 3:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded landscape path"
    trashed_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(trashed_path),
        timeout=12,
        description="landscape sample to be removed from integration favs.csv",
    )
    isolated_integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in isolated_integration_session.weird_dir.iterdir()),
        timeout=12,
        description="landscape sample to be moved into the integration weird dir",
    )


@pytest.mark.integration
def test_fun_time_portrait_trash_updates_temp_state(isolated_integration_session: FunTimeIntegrationSession):
    isolated_integration_session.write_dashboard_command("portrait_trash")
    chunk = isolated_integration_session.wait_for_new_log("Discarding from player 2:", timeout=12)
    match = re.search(r"Discarding from player 2:\s*(.+)", chunk)
    assert match, "Expected discard log chunk to include the discarded portrait path"
    trashed_path = Path(match.group(1).strip()).resolve()

    isolated_integration_session.wait_until(
        lambda: not isolated_integration_session.favs_contains(trashed_path),
        timeout=12,
        description="portrait sample to be removed from integration favs.csv",
    )
    isolated_integration_session.wait_until(
        lambda: any(p.name == trashed_path.name for p in isolated_integration_session.weird_dir.iterdir()),
        timeout=12,
        description="portrait sample to be moved into the integration weird dir",
    )

