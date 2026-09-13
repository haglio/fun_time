"""The pinned Fun Time shortcut is stamped with the session's taskbar identity."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from fun_time.orchestrator import stamp_shortcut_aumid
from fun_time.win32_taskbar import APP_USER_MODEL_ID


def test_the_pin_called_fun_time_is_stamped_by_its_whole_name():
    """"Fun Time VR.lnk" starts with our name and is still not ours to stamp.

    It was a second app's pin, with an AppUserModelID of its own, and it is a
    retired one now: the headset is entered by saying "enter VR" rather than by
    clicking anything.  A prefix would reach it, and stamping a pin we no longer
    launch through keeps it looking live on a taskbar it should be gone from.
    """
    with patch("fun_time.orchestrator.stamp_pinned_shortcuts", return_value={}) as stamp:
        stamp_shortcut_aumid()

    stamp.assert_called_once_with(APP_USER_MODEL_ID, ["Fun Time"])


def test_a_pin_windows_will_not_stamp_is_logged_and_the_session_goes_on(caplog):
    refused = {Path("C:/pins/Fun Time.lnk"): OSError("IPersistFile::Save failed")}

    with patch("fun_time.orchestrator.stamp_pinned_shortcuts", return_value=refused), \
            caplog.at_level(logging.WARNING):
        stamp_shortcut_aumid()

    assert "Could not stamp AppUserModelID" in caplog.text
