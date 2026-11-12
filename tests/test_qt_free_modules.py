from __future__ import annotations

import subprocess
import sys

_RULES_THE_SESSIONS_MESSAGE_FEED_READS = ("fun_time.message_placement",)
_TESTED_WITHOUT_A_QAPPLICATION = (
    "fun_time.log_panel_model",
    "fun_time.event_log",
    "fun_time.dashboard_layout",
    "fun_time.dashboard_bridge",
    "fun_time.dashboard_runtime",
)
QT_FREE = (*_RULES_THE_SESSIONS_MESSAGE_FEED_READS, *_TESTED_WITHOUT_A_QAPPLICATION)


def test_the_pure_halves_load_without_the_window_toolkit():
    probe = (
        "import sys; "
        + "".join(f"import {name}; " for name in QT_FREE)
        + "print(any(m == 'PySide6' or m.startswith('PySide6.') for m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "False", (
        "one of these pulled in PySide6; a widget import belongs in the module that "
        f"draws, not in {', '.join(QT_FREE)}"
    )
