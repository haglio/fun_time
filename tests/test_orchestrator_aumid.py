from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from fun_time.orchestrator import stamp_the_users_pin_outside_every_checkout
from fun_time.win32_taskbar import APP_USER_MODEL_ID


def test_the_pin_called_fun_time_is_stamped_by_its_whole_name():
    with patch("fun_time.orchestrator.stamp_pinned_shortcuts", return_value={}) as stamp:
        stamp_the_users_pin_outside_every_checkout()

    stamp.assert_called_once_with(APP_USER_MODEL_ID, ["Fun Time"])


def test_a_pin_windows_will_not_stamp_is_logged_and_the_session_goes_on(caplog):
    refused = {Path("C:/pins/Fun Time.lnk"): OSError("IPersistFile::Save failed")}

    with patch("fun_time.orchestrator.stamp_pinned_shortcuts", return_value=refused), \
            caplog.at_level(logging.WARNING):
        stamp_the_users_pin_outside_every_checkout()

    assert "Could not stamp AppUserModelID" in caplog.text
