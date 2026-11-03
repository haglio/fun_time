from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_HOW_THE_HEADSETS_GL_CHECKS_FOR_ERRORS = (
    "import fun_time_vr.render\n"
    "from OpenGL import _configflags\n"
    "print(_configflags.ERROR_CHECKING)\n"
)


def test_no_gl_call_of_the_headset_waits_for_the_driver_to_say_whether_it_failed():
    result = subprocess.run(
        [sys.executable, "-c", _HOW_THE_HEADSETS_GL_CHECKS_FOR_ERRORS],
        cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True,
        timeout=180, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
