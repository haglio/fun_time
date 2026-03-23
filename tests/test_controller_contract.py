from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_AHK = PROJECT_ROOT / "controller.ahk"


def _controller_text() -> str:
    return CONTROLLER_AHK.read_text(encoding="utf-8")


def test_controller_uses_manifest_argument_instead_of_positional_protocol():
    text = _controller_text()

    assert "if (A_Args.Length < 1)" in text
    assert 'CONTROLLER_MANIFEST_PATH := A_Args[1]' in text
    assert 'RequireManifestValue("executables", "vlc_exe")' in text
    assert "A_Args[29]" not in text
