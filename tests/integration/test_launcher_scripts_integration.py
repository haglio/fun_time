"""Every .vbs launcher compiles — checked by handing it to the real script host.

The unit suite reads these files as text and asserts on what is in them, which
cannot see a missing ``End If`` or a mistyped keyword.  VBScript compiles a whole
file before executing any of it, so a syntax error anywhere means the launcher
does nothing at all when it is double-clicked: the app never starts and the only
sign is a script-error dialog.

``cscript`` is the console host, and it reports a compilation error as text on
its output instead of in a dialog — which is what makes this assertable.  Each
script is run from a copy in a temp directory, where ``.venv`` does not exist, so
a script that *does* compile stops at its own missing-venv check instead of
launching anything.  That check ends in a ``MsgBox``, which blocks; the timeout
is the pass condition — reaching a dialog means compilation is long past.  It is
invisible either way: the integration suite runs on the hidden desktop.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CHECKOUT_DIR = Path(__file__).resolve().parents[2]
LAUNCHERS = ("launch.vbs", "launch_vr.vbs", "launch_branch.vbs")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="cscript is the Windows script host",
)


def _cscript_output(script: Path) -> str:
    command = [str(Path(os.environ["SystemRoot"]) / "System32" / "cscript.exe"), "//Nologo", str(script)]
    try:
        # Compiling is milliseconds' work and a failure exits at once, so a few
        # seconds is all it takes to tell "reported an error" from "running".
        finished = subprocess.run(command, capture_output=True, text=True, timeout=4)
    except subprocess.TimeoutExpired as blocked:
        # Blocked on its own missing-venv dialog, which is well past compiling.
        return f"{blocked.stdout or ''}{blocked.stderr or ''}"
    return finished.stdout + finished.stderr


@pytest.mark.parametrize("name", LAUNCHERS)
def test_the_launcher_compiles(name: str, tmp_path: Path):
    copy = tmp_path / name
    shutil.copyfile(CHECKOUT_DIR / name, copy)

    output = _cscript_output(copy)

    assert "compilation error" not in output.lower(), output
    assert "Expected" not in output, output
