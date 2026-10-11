from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence

from app_support.subprocess_utils import hidden_subprocess_kwargs
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
from shared_ui.preview_icon import app_icon

from fun_time.project_paths import PROJECT_ICON

from .main_verifier import claude_command

NOTICE_STAYS_MS = 30 * 60 * 1000


def open_in_claude_desktop(claude: str, short_id: str, session: str, cwd: str,
                           run: Callable[..., object] = subprocess.run) -> None:
    run([claude, "stop", short_id], cwd=cwd, capture_output=True, **hidden_subprocess_kwargs())
    run([claude, "--desktop", "--resume", session], cwd=cwd, capture_output=True, **hidden_subprocess_kwargs())


def main(argv: Sequence[str] | None = None) -> int:
    short_id, session, cwd, title, body = (sys.argv[1:] if argv is None else argv)[:5]
    app = QApplication([])
    tray = QSystemTrayIcon(app_icon(PROJECT_ICON, None))

    def opened() -> None:
        open_in_claude_desktop(claude_command(), short_id, session, cwd)
        app.quit()

    tray.messageClicked.connect(opened)
    tray.setToolTip(title)
    tray.show()
    tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 0)
    QTimer.singleShot(NOTICE_STAYS_MS, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
