from __future__ import annotations

import sys
from collections.abc import Sequence

from app_support.claude_sessions import claude_command, open_in_claude_desktop
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from shared_ui.preview_icon import app_icon

from fun_time.project_paths import PROJECT_ICON

MESSAGE_STAYS_MS = 30 * 60 * 1000


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
    QTimer.singleShot(MESSAGE_STAYS_MS, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
