"""Unit tests: integration teardown only ever kills its own desktop's processes.

The reap hits only windows on the hidden integration desktop — never the user's
real (input-desktop) session — which is what makes an unattended run safe.  It
has no other mode: the suite refuses to run anywhere but the hidden desktop, so
there is nowhere a by-name sweep of the machine could be the right answer.
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


def test_reap_off_the_hidden_desktop_kills_nothing_at_all():
    """The last machine-wide kill in the suite, and the same shape as the reap
    that took down the user's players.

    Off the hidden desktop this used to run `Get-Process pythonw,autohotkey64 |
    where StartTime > -5min | Stop-Process -Force`, which is every satellite,
    Nau, Genau, the dashboard, the audio companion and the AHK bridge of any
    session started in the last five minutes — the user's included.  It existed
    to support bare `pytest tests/integration/`, which is exactly the invocation
    that is forbidden; the suite now refuses that run instead of sweeping.
    """
    with patch.object(integration_support, "current_desktop_name", return_value="Default"), \
         patch.object(integration_support, "pids_with_window_on_current_desktop") as pids, \
         patch.object(integration_support, "kill_process_tree") as kill, \
         patch.object(integration_support.subprocess, "run") as run:
        integration_support._kill_leftover_app_processes()

    kill.assert_not_called()
    pids.assert_not_called()
    run.assert_not_called()
