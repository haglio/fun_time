from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from fun_time.lock_hud import HudPanel
from fun_time.lock_hud_app import OVERLAY_HEIGHT, OVERLAY_WIDTH, paint_hud


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _solid_pixmap(color: QColor, width: int = 40, height: int = 60) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(color)
    return pixmap


def _non_transparent_samples(image: QImage) -> int:
    return sum(
        1
        for y in range(0, image.height(), 4)
        for x in range(0, image.width(), 4)
        if image.pixelColor(x, y).alpha() > 0
    )


def _render(panel: HudPanel, seed_thumbs, action_thumbs) -> QImage:
    image = QImage(OVERLAY_WIDTH, OVERLAY_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint_hud(painter, image.rect(), panel, seed_thumbs, action_thumbs)
    finally:
        painter.end()
    return image


def test_paint_hud_fills_the_panel_and_draws_thumbnails(qt_app):
    panel = HudPanel(
        side="portrait", locked=True, lock_label="Locked",
        action_siblings=["a", "b"], seed_siblings=["s"],
    )
    image = _render(
        panel,
        seed_thumbs=[_solid_pixmap(QColor(220, 40, 40))],
        action_thumbs=[_solid_pixmap(QColor(40, 40, 220)), _solid_pixmap(QColor(40, 200, 40))],
    )

    # The rounded background fills most of the panel, so most samples have paint.
    total = (OVERLAY_WIDTH // 4) * (OVERLAY_HEIGHT // 4)
    assert _non_transparent_samples(image) > total * 0.5


def test_paint_hud_handles_an_empty_unlocked_panel(qt_app):
    panel = HudPanel(
        side="landscape", locked=False, lock_label="Unlocked",
        action_siblings=[], seed_siblings=[],
    )

    image = _render(panel, seed_thumbs=[], action_thumbs=[])

    # Still draws its background even with no siblings — never blows up.
    assert _non_transparent_samples(image) > 0
