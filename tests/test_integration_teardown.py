"""Unit tests: integration teardown scopes its leftover-process kill by desktop.

On the hidden integration desktop the reap must hit only that desktop's own
windows — never the user's real (input-desktop) session — which is what makes an
unattended run safe.  On a visible manual run it falls back to the by-name +
5-minute-recency PowerShell sweep.
"""
import os
import sys
from unittest.mock import patch

from tests.integration import integration_support
from tests.integration.hidden_desktop import HIDDEN_DESKTOP_NAME


def test_reap_on_hidden_desktop_kills_the_app_windows_but_never_a_pytest():
    """pytest runs as python.exe and owns real Qt windows on this desktop — both
    this run's and any run queued behind it.  Killing one leaves a suite dead
    with no output, so the reap only ever targets the images the apps run as.
    """
    own = os.getpid()
    images = {
        222: r"C:\blah\genau\.venv\Scripts\pythonw.exe",
        333: r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
        444: r"C:\blah\fun_time\.venv\Scripts\python.exe",  # a queued run's pytest
        555: None,                                          # window owner already exited
        own: sys.executable,
    }
    with patch.object(integration_support, "current_desktop_name", return_value=HIDDEN_DESKTOP_NAME), \
         patch.object(integration_support, "pids_with_window_on_current_desktop", return_value=set(images)), \
         patch.object(integration_support, "get_process_image_name", images.get), \
         patch.object(integration_support, "kill_process_tree") as kill, \
         patch.object(integration_support.subprocess, "run") as run:
        integration_support._kill_leftover_app_processes()

    killed = {call.args[0] for call in kill.call_args_list}
    assert killed == {222, 333}  # the leftover satellite/Nau/dashboard + AHK
    assert 444 not in killed     # never another run's pytest
    assert own not in killed     # never the running pytest process itself
    run.assert_not_called()      # never the global by-name sweep on the hidden desktop


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


def test_both_reaps_target_the_same_app_images():
    """The by-name sweep and the desktop sweep must agree on what an app is —
    python.exe (pytest, the orchestrator) is in neither."""
    with patch.object(integration_support, "current_desktop_name", return_value="Default"), \
         patch.object(integration_support.subprocess, "run") as run:
        integration_support._kill_leftover_app_processes()

    command = run.call_args.args[0][-1]
    names = set(command.split("Get-Process ", 1)[1].split(" ", 1)[0].split(","))
    assert names == {"autohotkey64", "pythonw"}
    assert "python" not in names
