"""Unit tests: integration teardown scopes its leftover-process kill by desktop.

On the hidden integration desktop the reap must hit only that desktop's own
windows — never the user's real (input-desktop) session — which is what makes an
unattended run safe.  On a visible manual run it falls back to the by-name +
5-minute-recency PowerShell sweep.
"""
import os
from unittest.mock import patch

from tests.integration import integration_support
from tests.integration.hidden_desktop import HIDDEN_DESKTOP_NAME


def test_reap_on_hidden_desktop_kills_this_desktops_pids_but_never_itself():
    own = os.getpid()
    with patch.object(integration_support, "current_desktop_name", return_value=HIDDEN_DESKTOP_NAME), \
         patch.object(integration_support, "pids_with_window_on_current_desktop", return_value={111, 222, own}), \
         patch.object(integration_support, "kill_process_tree") as kill, \
         patch.object(integration_support.subprocess, "run") as run:
        integration_support._kill_leftover_app_processes()
    killed = {call.args[0] for call in kill.call_args_list}
    assert killed == {111, 222}          # the session's own app processes
    assert own not in killed             # never the running pytest process itself
    run.assert_not_called()              # never the global by-name sweep on the hidden desktop


def test_reap_on_real_desktop_uses_the_byname_sweep():
    with patch.object(integration_support, "current_desktop_name", return_value="Default"), \
         patch.object(integration_support, "pids_with_window_on_current_desktop") as pids, \
         patch.object(integration_support, "kill_process_tree") as kill, \
         patch.object(integration_support.subprocess, "run") as run:
        integration_support._kill_leftover_app_processes()
    kill.assert_not_called()
    pids.assert_not_called()
    assert run.call_count == 1
    cmd = run.call_args.args[0]
    assert "powershell.exe" in cmd[0]
    assert "Stop-Process" in cmd[-1]
