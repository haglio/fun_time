from __future__ import annotations

import html
from collections.abc import Callable
from functools import partial

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QDialog, QTextBrowser, QToolTip, QVBoxLayout, QWidget
from shared_ui.find_bar import FindBar
from shared_ui.preview_icon import app_icon
from shared_ui.spacing import MARGIN_STANDARD

from fun_time import preview_marker
from fun_time.command_reference import (
    NARROWEST_PAGE_PX,
    reference_details,
    render_reference_html,
)
from fun_time.project_paths import PROJECT_ICON
from fun_time.win32 import keep_in_topmost_band

REFERENCE_WINDOW_TITLE = "Hotkeys & Voice Commands Reference"


class ReferenceBrowser(QTextBrowser):
    def __init__(self, parent: QWidget | None = None, *, tooltip=QToolTip,
                 on_rerendered: Callable[[], None] = lambda: None) -> None:
        super().__init__(parent)
        self._show_tip, self._hide_tip = tooltip.showText, tooltip.hideText
        self._on_rerendered = on_rerendered
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setMinimumWidth(NARROWEST_PAGE_PX + self._width_around_the_page())
        self._detail_of = reference_details().get
        self._page_width = 0
        self._render()

    def _width_around_the_page(self) -> int:
        return (2 * (self.frameWidth() + round(self.document().documentMargin()))
                + self.verticalScrollBar().sizeHint().width())

    def _render(self) -> None:
        page = max(NARROWEST_PAGE_PX,
                   self.viewport().width() - 2 * round(self.document().documentMargin()))
        if page == self._page_width:
            return
        self._page_width = page
        scrolled = self.verticalScrollBar().value()
        self.setHtml(render_reference_html(page))
        self.verticalScrollBar().setValue(scrolled)
        self._on_rerendered()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def detail_at(self, viewport_pos) -> str:
        return self._detail_of(self.anchorAt(viewport_pos), "")

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            detail = self.detail_at(self.viewport().mapFromGlobal(event.globalPos()))
            if detail:
                self._show_tip(event.globalPos(), f"<p>{html.escape(detail)}</p>", self)
            else:
                self._hide_tip()
            return True
        return super().event(event)


REFERENCE_FIRST_SIZE = QSize(1000, 800)
ROOM_FOR_THE_FRAME_PX = 48


def reference_first_size(available: QSize, *, at_least_wide: int) -> QSize:
    return QSize(max(at_least_wide, min(REFERENCE_FIRST_SIZE.width(),
                                        available.width() - ROOM_FOR_THE_FRAME_PX)),
                 min(REFERENCE_FIRST_SIZE.height(), available.height() - ROOM_FOR_THE_FRAME_PX))


class ReferenceDialog(QDialog):
    typing_invited = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(REFERENCE_WINDOW_TITLE)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowIcon(app_icon(PROJECT_ICON, preview_marker.shown_as()))
        find_bar = FindBar("Find:", "a key or a word…")
        find_bar.setContentsMargins(MARGIN_STANDARD, 0, MARGIN_STANDARD, MARGIN_STANDARD)
        find_bar.typing_invited.connect(self.typing_invited)
        browser = ReferenceBrowser(self, on_rerendered=find_bar.refresh)
        self.open_find = partial(find_bar.open_find, [browser])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(browser)
        layout.addWidget(find_bar)
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self.open_find)
        self.setMinimumWidth(browser.minimumWidth())
        screen = (QGuiApplication.primaryScreen() if parent is None else parent.screen())
        self.resize(reference_first_size(screen.availableGeometry().size(),
                                         at_least_wide=self.minimumWidth()))

    def sync_topmost(self, omni_paused: bool) -> None:
        keep_in_topmost_band(int(self.winId()), topmost=not omni_paused)
