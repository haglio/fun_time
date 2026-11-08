from __future__ import annotations

import subprocess
import sys

from fun_time.log_panel import LogPanelWidget
from fun_time.message_feed import FLASH_POLL_MS


def test_a_message_flashes_before_the_panel_lists_it():
    assert FLASH_POLL_MS < LogPanelWidget.POLL_MS


def test_the_feeds_rules_run_without_qt():
    probe = ("import sys, fun_time.message_feed; "
             "print(sorted(m for m in sys.modules if m.startswith('PyQt6')))")

    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            check=True)

    assert result.stdout.strip() == "[]"
