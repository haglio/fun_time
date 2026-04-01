"""Tests for orchestrator shortcut AUMID stamping."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from fun_time.orchestrator import stamp_shortcut_aumid
from fun_time.win32 import APP_USER_MODEL_ID, _read_shortcut_app_user_model_id


def _create_lnk(path: Path) -> None:
    """Create a minimal .lnk file via PowerShell."""
    target = os.environ.get("COMSPEC", "cmd.exe")
    subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-Command",
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{path}'); "
            f"$s.TargetPath = '{target}'; "
            f"$s.Save()",
        ],
        check=True,
        capture_output=True,
    )


def test_stamps_project_shortcut(tmp_path):
    lnk = tmp_path / "Fun Time.lnk"
    _create_lnk(lnk)

    empty_dir = tmp_path / "no_pins"
    empty_dir.mkdir()
    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=empty_dir):
        stamp_shortcut_aumid(project_dir=tmp_path)

    assert _read_shortcut_app_user_model_id(str(lnk)) == APP_USER_MODEL_ID


def test_stamps_pinned_shortcut(tmp_path):
    """If a Fun Time .lnk exists in the taskbar pin folder, stamp it too."""
    fake_pin_dir = tmp_path / "pins"
    fake_pin_dir.mkdir()
    lnk = fake_pin_dir / "Fun Time.lnk"
    _create_lnk(lnk)

    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=fake_pin_dir):
        stamp_shortcut_aumid(project_dir=tmp_path)

    assert _read_shortcut_app_user_model_id(str(lnk)) == APP_USER_MODEL_ID


def test_no_crash_when_no_shortcuts(tmp_path):
    """No .lnk files at all — should not crash."""
    empty_dir = tmp_path / "no_pins"
    empty_dir.mkdir()
    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=empty_dir):
        stamp_shortcut_aumid(project_dir=tmp_path)


def test_skips_unrelated_shortcuts(tmp_path):
    """Only stamps shortcuts with 'Fun' in the name."""
    fake_pin_dir = tmp_path / "pins"
    fake_pin_dir.mkdir()
    unrelated = fake_pin_dir / "Chrome.lnk"
    _create_lnk(unrelated)

    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=fake_pin_dir):
        stamp_shortcut_aumid(project_dir=tmp_path)

    # Unrelated shortcut should not have been stamped
    assert _read_shortcut_app_user_model_id(str(unrelated)) is None
