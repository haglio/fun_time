from __future__ import annotations

import subprocess
import sys


def test_the_feeds_rules_run_without_qt():
    probe = ("import sys, fun_time.message_feed; "
             "print(sorted(m for m in sys.modules if m.startswith('PySide6')))")

    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            check=True)

    assert result.stdout.strip() == "[]"
