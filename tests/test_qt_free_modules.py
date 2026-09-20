"""The parts of the panel code that must load without a window toolkit.

Two GUI modules used to split themselves in half with a second block of PyQt6
imports written 128 and 57 lines in — a real convention, for a good reason (the
upper half tests without a QApplication, and the session's own notice feed reads
the placement rules), with nothing enforcing it. A module boundary enforces it,
and this is what says so.
"""
from __future__ import annotations

import subprocess
import sys

QT_FREE = (
    "fun_time.log_panel_model",
    "fun_time.notice_placement",
    "fun_time.event_log",
    "fun_time.dashboard_layout",
    "fun_time.dashboard_bridge",
    "fun_time.dashboard_runtime",
)


def test_the_pure_halves_load_without_the_window_toolkit():
    probe = (
        "import sys; "
        + "".join(f"import {name}; " for name in QT_FREE)
        + "print(any(m == 'PyQt6' or m.startswith('PyQt6.') for m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "False", (
        "one of these pulled in PyQt6; a widget import belongs in the module that "
        f"draws, not in {', '.join(QT_FREE)}"
    )
