"""What kind of session this is, read once at the process edge."""
from __future__ import annotations

from fun_time.session_environment import SessionEnvironment


class TestFromEnviron:
    def test_a_plain_environment_is_a_production_session(self):
        env = SessionEnvironment.from_environ({})

        assert env == SessionEnvironment(
            integration=False, show_overlays=True, dashboard_enabled=True)

    def test_an_integration_run_gets_no_curtain(self):
        env = SessionEnvironment.from_environ({"FUN_TIME_RUN_INTEGRATION": "1"})

        assert env.integration is True
        assert env.show_overlays is False

    def test_an_integration_run_can_ask_for_the_curtain_back(self):
        """The hidden desktop tests the exact startup a real session takes."""
        env = SessionEnvironment.from_environ(
            {"FUN_TIME_RUN_INTEGRATION": "1", "FUN_TIME_INTEGRATION_OVERLAYS": "1"})

        assert env.integration is True
        assert env.show_overlays is True

    def test_the_dashboard_switch_is_off_only_when_it_says_one(self):
        assert SessionEnvironment.from_environ(
            {"FUN_TIME_DISABLE_DASHBOARD": "1"}).dashboard_enabled is False
        assert SessionEnvironment.from_environ(
            {"FUN_TIME_DISABLE_DASHBOARD": "0"}).dashboard_enabled is True
