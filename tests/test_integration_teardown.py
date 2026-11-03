from __future__ import annotations

import os
import sys
from unittest.mock import patch

from tests.integration import integration_support
from tests.integration.hidden_desktop import HIDDEN_DESKTOP_NAME


def test_reap_on_hidden_desktop_kills_the_app_windows_but_never_a_pytest():
    own = os.getpid()
    images = {
        222: r"C:\blah\genau\.venv\Scripts\pythonw.exe",
        333: r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
        444: r"C:\blah\fun_time\.venv\Scripts\python.exe",  # a queued run's pytest
        555: None,
        666: r"C:\Python314\python.exe",
        own: sys.executable,
    }
    command_lines = {444: "python.exe -m pytest tests/integration/",
                     666: r"python.exe -m origenerator --hosted C:\state"}
    asked: list[int] = []

    def command_line_of(pid: int) -> str | None:
        asked.append(pid)
        return command_lines.get(pid)

    with patch.object(integration_support, "current_desktop_name", return_value=HIDDEN_DESKTOP_NAME), \
         patch.object(integration_support, "pids_with_window_on_current_desktop", return_value=set(images)), \
         patch.object(integration_support, "get_process_image_name", images.get), \
         patch.object(integration_support, "command_line_of", command_line_of), \
         patch.object(integration_support, "kill_process_tree") as kill:
        integration_support._kill_leftover_app_processes()

    killed = {call.args[0] for call in kill.call_args_list}
    assert killed == {222, 333, 666}
    assert 444 not in killed     # never another run's pytest
    assert own not in killed     # never the running pytest process itself
    assert set(asked) <= set(images)


def test_reap_off_the_hidden_desktop_kills_nothing_at_all():
    with patch.object(integration_support, "current_desktop_name", return_value="Default"), \
         patch.object(integration_support, "pids_with_window_on_current_desktop") as pids, \
         patch.object(integration_support, "kill_process_tree") as kill, \
         patch.object(integration_support, "command_line_of") as asked:
        integration_support._kill_leftover_app_processes()

    kill.assert_not_called()
    pids.assert_not_called()
    asked.assert_not_called()
