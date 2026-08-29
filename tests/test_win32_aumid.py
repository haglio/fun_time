"""Tests for AppUserModelID functions in win32 module."""
from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from fun_time.win32 import APP_USER_MODEL_ID, set_app_user_model_id, set_shortcut_app_user_model_id


class TestSetAppUserModelId:
    def test_calls_shell32_with_correct_id(self):
        with patch("fun_time.win32._shell32") as mock_shell32:
            mock_shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
            set_app_user_model_id(APP_USER_MODEL_ID)
            mock_shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
                APP_USER_MODEL_ID
            )

    def test_raises_on_failure(self):
        with patch("fun_time.win32._shell32") as mock_shell32:
            # E_FAIL as signed 32-bit (HRESULT is signed; FAILED() checks < 0)
            mock_shell32.SetCurrentProcessExplicitAppUserModelID.return_value = -2147467259
            with pytest.raises(OSError, match="SetCurrentProcessExplicitAppUserModelID failed"):
                set_app_user_model_id("Bad.Id")

    def test_the_app_identity_is_the_one_the_pinned_shortcut_carries(self):
        assert APP_USER_MODEL_ID == "FunTime.App"


class TestSetShortcutAppUserModelId:
    def test_stamps_real_lnk_file(self, tmp_path):
        """Create a real .lnk, stamp it, read back — round-trip on the real COM stack."""
        lnk_path = tmp_path / "Test.lnk"
        # Create a minimal .lnk via PowerShell
        target = os.environ.get("COMSPEC", "cmd.exe")
        subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                f"$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut('{lnk_path}'); "
                f"$s.TargetPath = '{target}'; "
                f"$s.Save()",
            ],
            check=True,
            capture_output=True,
        )
        assert lnk_path.exists()

        # Stamp the AUMID
        set_shortcut_app_user_model_id(str(lnk_path), "Test.AppId")

        # Read back via IPropertyStore to verify
        from fun_time.win32 import _read_shortcut_app_user_model_id

        assert _read_shortcut_app_user_model_id(str(lnk_path)) == "Test.AppId"

    def test_nonexistent_file_raises(self, tmp_path):
        bad_path = str(tmp_path / "no_such.lnk")
        with pytest.raises(OSError):
            set_shortcut_app_user_model_id(bad_path, "X")
