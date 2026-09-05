"""Tests for orchestrator shortcut AUMID stamping."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from app_support.win32 import read_shortcut_app_user_model_id

from fun_time.orchestrator import stamp_shortcut_aumid
from fun_time.win32_taskbar import APP_USER_MODEL_ID


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


def test_stamps_pinned_shortcut(tmp_path):
    """If a Fun Time .lnk exists in the taskbar pin folder, stamp it."""
    fake_pin_dir = tmp_path / "pins"
    fake_pin_dir.mkdir()
    lnk = fake_pin_dir / "Fun Time.lnk"
    _create_lnk(lnk)

    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=fake_pin_dir):
        stamp_shortcut_aumid()

    assert read_shortcut_app_user_model_id(str(lnk)) == APP_USER_MODEL_ID


def test_no_crash_when_no_shortcuts(tmp_path):
    """No .lnk files at all — should not crash."""
    empty_dir = tmp_path / "no_pins"
    empty_dir.mkdir()
    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=empty_dir):
        stamp_shortcut_aumid()


def test_skips_unrelated_shortcuts(tmp_path):
    """Another app's pin is left alone."""
    fake_pin_dir = tmp_path / "pins"
    fake_pin_dir.mkdir()
    unrelated = fake_pin_dir / "Chrome.lnk"
    _create_lnk(unrelated)

    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=fake_pin_dir):
        stamp_shortcut_aumid()

    # Unrelated shortcut should not have been stamped
    assert read_shortcut_app_user_model_id(str(unrelated)) is None


def test_leaves_the_vr_pin_its_own_identity(tmp_path):
    """"Fun Time VR.lnk" starts with our name and is not ours to stamp.

    Both pins sit in the same folder, so a prefix or "contains" match would
    give the VR session the desktop app's AUMID -- and Windows reads one AUMID
    as one app, collapsing the V and the FT into a single taskbar button.
    """
    fake_pin_dir = tmp_path / "pins"
    fake_pin_dir.mkdir()
    ours = fake_pin_dir / "Fun Time.lnk"
    vr = fake_pin_dir / "Fun Time VR.lnk"
    _create_lnk(ours)
    _create_lnk(vr)

    with patch("fun_time.orchestrator._taskbar_pin_dir", return_value=fake_pin_dir):
        stamp_shortcut_aumid()

    assert read_shortcut_app_user_model_id(str(ours)) == APP_USER_MODEL_ID
    assert read_shortcut_app_user_model_id(str(vr)) is None
