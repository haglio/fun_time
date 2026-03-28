from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_launch_broker_tray_vbs_targets_tray_script():
    text = _read("launch_broker_tray.vbs")

    assert "scripts\\broker_tray.ps1" in text
    assert "shell.Run cmd, 0, False" in text


def test_install_broker_startup_task_uses_tray_launcher():
    text = _read("scripts/install_broker_startup_task.ps1")

    assert "launch_broker_tray.vbs" in text
    assert "wscript.exe" in text
    assert "run_broker_service.ps1" not in text


def test_install_fallback_generates_vbs_with_absolute_broker_tray_path():
    """The startup-folder fallback must generate a VBS with an absolute path to
    broker_tray.ps1 — NOT copy launch_broker_tray.vbs, which uses ScriptFullName
    and would resolve to the wrong directory when run from the Startup folder."""
    text = _read("scripts/install_broker_startup_task.ps1")

    # Fallback must reference broker_tray.ps1 (the real target)
    assert "broker_tray.ps1" in text
    # Must NOT blindly copy the launcher VBS (ScriptFullName bug)
    assert "Copy-Item" not in text
