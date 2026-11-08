from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from fun_time.session_environment import SessionEnvironment

REPO = Path(__file__).resolve().parents[1]
PACKAGES = ("fun_time", "fun_time_vr", "main_funestra", "satellite")


class TestFromEnviron:
    def test_a_plain_environment_is_a_production_session(self):
        env = SessionEnvironment.from_environ({})

        assert env == SessionEnvironment(
            integration=False, show_overlays=True, dashboard_enabled=True)

    def test_an_integration_run_gets_no_loading_screen(self):
        env = SessionEnvironment.from_environ({"FUN_TIME_RUN_INTEGRATION": "1"})

        assert env.integration is True
        assert env.show_overlays is False

    def test_an_integration_run_can_ask_for_the_loading_screen_back(self):
        env = SessionEnvironment.from_environ(
            {"FUN_TIME_RUN_INTEGRATION": "1", "FUN_TIME_INTEGRATION_OVERLAYS": "1"})

        assert env.integration is True
        assert env.show_overlays is True

    def test_the_dashboard_switch_is_off_only_when_it_says_one(self):
        assert SessionEnvironment.from_environ(
            {"FUN_TIME_DISABLE_DASHBOARD": "1"}).dashboard_enabled is False
        assert SessionEnvironment.from_environ(
            {"FUN_TIME_DISABLE_DASHBOARD": "0"}).dashboard_enabled is True


def test_the_switches_outside_session_environment_are_those_children_inherit():
    read_in: dict[str, set[str]] = defaultdict(set)
    for package in PACKAGES:
        for path in (REPO / package).rglob("*.py"):
            for switch in re.findall(r'"(FUN_TIME_[A-Z_]+)"', path.read_text(encoding="utf-8")):
                read_in[switch].add(path.relative_to(REPO).as_posix())

    at_the_edge = {switch for switch, where in read_in.items()
                   if where == {"fun_time/session_environment.py"}}
    assert at_the_edge == {
        "FUN_TIME_RUN_INTEGRATION", "FUN_TIME_INTEGRATION_OVERLAYS", "FUN_TIME_DISABLE_DASHBOARD"}
    assert {switch: where for switch, where in read_in.items() if switch not in at_the_edge} == {
        "FUN_TIME_MUTE_AUDIO": {
            "fun_time/audio_companion_app.py", "main_funestra/cli.py", "satellite/cli.py"},
        "FUN_TIME_FAKE_MONITORS": {"fun_time/monitors.py"},
        "FUN_TIME_BRANCH_SESSION": {"fun_time/preview_marker.py"},
    }
