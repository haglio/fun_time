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


def _panel(**overrides) -> HudPanel:
    base = dict(
        side="portrait", locked=True, lock_label="Locked",
        current="C:/vids/cur.mp4", seed_siblings=["s1", "s2"], action_siblings=["a1"],
    )
    base.update(overrides)
    return HudPanel(**base)


def _render(panel: HudPanel, current_thumb, seed_thumbs, action_thumbs) -> QImage:
    image = QImage(OVERLAY_WIDTH, OVERLAY_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint_hud(painter, image.rect(), panel, current_thumb, seed_thumbs, action_thumbs)
    finally:
        painter.end()
    return image


def _samples(image: QImage, predicate) -> int:
    return sum(
        1
        for y in range(0, image.height(), 2)
        for x in range(0, image.width(), 2)
        if predicate(image.pixelColor(x, y))
    )


def _is_near_white(color: QColor) -> bool:
    return color.red() > 248 and color.green() > 248 and color.blue() > 248 and color.alpha() > 200


def test_paint_hud_fills_the_panel_and_draws_the_map(qt_app):
    image = _render(
        _panel(),
        current_thumb=_solid_pixmap(QColor(200, 120, 150)),
        seed_thumbs=[_solid_pixmap(QColor(220, 40, 40))],
        action_thumbs=[_solid_pixmap(QColor(40, 40, 220))],
    )

    total = (OVERLAY_WIDTH // 2) * (OVERLAY_HEIGHT // 2)
    assert _samples(image, lambda c: c.alpha() > 0) > total * 0.5


def test_paint_hud_borders_the_current_clip_in_white(qt_app):
    """The corner (current) thumbnail gets a white outline nothing else has."""
    # Unlocked so the lock label is muted grey, not near-white; the border is
    # then the only near-white ink on the panel.
    panel = _panel(locked=False, lock_label="Unlocked")

    with_current = _render(panel, _solid_pixmap(QColor(30, 30, 30)), [], [])
    without_current = _render(panel, None, [], [])

    assert _samples(with_current, _is_near_white) > 0
    assert _samples(without_current, _is_near_white) == 0


def test_paint_hud_without_a_current_thumb_still_draws_its_shell(qt_app):
    image = _render(_panel(current="", seed_siblings=[], action_siblings=[]), None, [], [])

    assert _samples(image, lambda c: c.alpha() > 0) > 0
