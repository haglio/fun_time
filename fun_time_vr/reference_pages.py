from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtGui import QColor, QImage, QPainter, QTextDocument
from PyQt6.QtWidgets import QApplication, QTextBrowser
from shared_ui.application import application
from shared_ui.palette import BG_PRIMARY
from shared_ui.reference import ReferenceSection, narrowest_page_px, render_reference_html

from fun_time.command_reference import build_reference_sections
from fun_time.hosted_contract import published_actions_heard_as

from .offscreen_qt import as_pil
from .reference_panel import ReferencePage, ReferencePages, write_pages


def draw_page(section: ReferenceSection, every_section: tuple[ReferenceSection, ...]) -> ReferencePage:
    width = narrowest_page_px(every_section)
    document = QTextDocument()
    document.setDocumentMargin(0)
    document.setHtml(render_reference_html((section,), width))
    document.setTextWidth(width)
    image = QImage(width, round(document.size().height()), QImage.Format.Format_RGBA8888)
    image.fill(QColor(*BG_PRIMARY))
    painter = QPainter(image)
    try:
        document.drawContents(painter)
    finally:
        painter.end()
    title = document.documentLayout().blockBoundingRect(document.firstBlock())
    return ReferencePage(section.title, as_pil(image), round(title.bottom()), section.note)


def _a_wheel_notch_on_the_desktops_reference_px() -> int:
    browser = QTextBrowser()
    try:
        return browser.verticalScrollBar().singleStep() * QApplication.wheelScrollLines()
    finally:
        browser.deleteLater()


def draw_the_pages(sections: tuple[ReferenceSection, ...]) -> ReferencePages:
    return ReferencePages(tuple(draw_page(section, sections) for section in sections),
                          _a_wheel_notch_on_the_desktops_reference_px())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The command reference's pages, drawn for the headset")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, opened=application) -> int:
    args = parse_args(argv)
    with opened([sys.argv[0]]):
        sections = build_reference_sections(published_actions_heard_as(args.state_dir))
        write_pages(draw_the_pages(sections), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
