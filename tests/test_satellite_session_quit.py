from __future__ import annotations

from funestra.session_quit import SESSION_QUIT

from fun_time.command_answering import LOOP_COMMANDS
from fun_time.dashboard_actions import QUIT_BUTTON


def test_the_ask_is_the_dashboards_own_quit_verb():
    assert SESSION_QUIT == QUIT_BUTTON


def test_a_closed_satellite_asks_for_something_the_session_answers():
    assert SESSION_QUIT in LOOP_COMMANDS, (
        f"a closed satellite asks for {SESSION_QUIT!r} and nothing answers: "
        "the close goes quiet instead of ending the session")

