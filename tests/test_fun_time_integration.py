from __future__ import annotations

import sys

import pytest

from .integration_support import FunTimeIntegrationSession, build_integration_config, integration_enabled


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not integration_enabled(),
    reason="Fun Time integration tests are opt-in and require a real Windows runtime",
)


@pytest.mark.integration
def test_fun_time_core_runtime_smoke(tmp_path):
    config_path = build_integration_config(tmp_path)
    session = FunTimeIntegrationSession(config_path)

    try:
        session.start()

        session.write_dashboard_command("portrait_lock")
        session.wait_for_new_log("Locked portrait VLC", timeout=12)

        session.write_dashboard_command("portrait_lock")
        session.wait_for_new_log("Unlocked portrait VLC", timeout=12)

        session.write_dashboard_command("omnipause_toggle")
        session.wait_for_new_log("OmniPause: entering", timeout=12)

        session.write_dashboard_command("omnipause_toggle")
        session.wait_for_new_log("OmniPause: leaving", timeout=12)

        session.write_dashboard_command("fmode_toggle")
        session.wait_for_new_log("F-mode hotkey: enabled", timeout=12)

        session.write_dashboard_command("fmode_toggle")
        session.wait_for_new_log("F-mode hotkey: disabled", timeout=12)

        session.write_dashboard_command("robot_toggle")
        session.wait_for_new_log("Robot Hand hotkey: disabled", timeout=12)

        session.write_dashboard_command("robot_toggle")
        session.wait_for_new_log("Robot Hand hotkey: enabled", timeout=12)

        session.write_dashboard_command("portrait_trash")
        session.wait_for_new_log("Discarding from player 2:", timeout=12)
    finally:
        session.stop()
