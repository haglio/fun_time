from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from fun_time.config import LayoutConfig
from fun_time.lock_hud import HudAppConfig, HudPanel
from fun_time.lock_hud_app import (
    _COL_LABEL_H,
    _LOCK_BAND_H,
    _PAD,
    _ROW_LABEL_W,
    HudOverlay,
    LockHud,
    OVERLAY_HEIGHT,
    OVERLAY_WIDTH,
    build_click_targets,
    hit_test_targets,
    hud_thumbnail_rects,
    paint_hud,
)
from fun_time.window_layout import WindowRect


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


def test_hud_thumbnail_rects_positions_the_map_and_drops_overflow():
    """The corner anchors the map; seeds walk right and actions walk down, each
    dropped (not clipped) when it would cross the panel edge."""
    from fun_time.lock_hud_app import _MAP_GAP

    corner, seeds, actions = hud_thumbnail_rects(
        map_x=100, map_y=50, right=300, bottom=280,
        corner_size=(30, 54),
        seed_sizes=[(30, 54), (30, 54), (200, 54)],   # the third would cross right=300
        action_sizes=[(30, 54), (30, 200)],           # the second would cross bottom=280
    )

    assert corner == (100, 50, 30, 54)
    s1 = 100 + 30 + _MAP_GAP
    s2 = s1 + 30 + _MAP_GAP
    assert seeds == [(s1, 50, 30, 54), (s2, 50, 30, 54)]  # third dropped
    assert actions == [(100, 50 + 54 + _MAP_GAP, 30, 54)]  # second dropped


def test_build_and_hit_test_click_targets():
    """Targets zip the drawn rects to their paths — corner=current, then each
    seed, then each action — and a point resolves to the clip it falls in."""
    corner = (10, 10, 20, 20)
    seeds = [(40, 10, 20, 20)]
    actions = [(10, 40, 20, 20)]

    targets = build_click_targets(corner, seeds, actions, "cur.mp4", ["s1.mp4"], ["a1.mp4"])

    assert targets == [
        ((10, 10, 20, 20), "cur.mp4"),
        ((40, 10, 20, 20), "s1.mp4"),
        ((10, 40, 20, 20), "a1.mp4"),
    ]
    assert hit_test_targets(targets, 15, 15) == "cur.mp4"
    assert hit_test_targets(targets, 45, 15) == "s1.mp4"
    assert hit_test_targets(targets, 15, 45) == "a1.mp4"
    assert hit_test_targets(targets, 100, 100) == ""  # empty area hits nothing


def test_build_click_targets_skips_a_missing_corner():
    assert build_click_targets(None, [], [], "cur.mp4", [], []) == []


def test_clicking_a_thumbnail_posts_a_play_command(qt_app):
    """A press inside a target's rect posts "<side>_play_video|<path>"; a press
    in empty space posts nothing."""
    sent: list[str] = []
    overlay = HudOverlay("landscape", sent.append)
    try:
        overlay._click_targets = [((0, 0, 30, 30), "C:/vids/pick.mp4")]

        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(10, 10)))
        assert sent == ["landscape_play_video|C:/vids/pick.mp4"]

        sent.clear()
        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(200, 200)))
        assert sent == []
    finally:
        overlay.close()


def _label_ink_in_rect(image: QImage, x0: int, y0: int, x1: int, y1: int) -> int:
    """Pixels in the region that carry label text — lighter than the dark panel
    background (24) but not the white border, i.e. the muted-grey glyphs."""
    return sum(
        1
        for y in range(max(0, y0), min(image.height(), y1))
        for x in range(max(0, x0), min(image.width(), x1))
        if 70 < image.pixelColor(x, y).red() < 200
    )


def test_paint_hud_labels_seed_columns_and_action_rows(qt_app):
    """Row labels (the action names) live in the left gutter, so naming the
    rows adds ink there; column labels ("Seed 1", "Seed 2", …) live in the
    header strip, so more seed columns add more label ink up top."""
    thumb = _solid_pixmap(QColor(30, 30, 30))
    map_top = _PAD + _LOCK_BAND_H  # no filter line on these panels

    named = _panel(current="c.mp4", seed_siblings=[], action_siblings=["a1"],
                   current_action="Alpha", action_labels=("Delta",))
    unnamed = _panel(current="c.mp4", seed_siblings=[], action_siblings=["a1"],
                     current_action="", action_labels=("",))

    def gutter(image: QImage) -> int:
        return _label_ink_in_rect(image, _PAD, map_top, _PAD + _ROW_LABEL_W, OVERLAY_HEIGHT)

    assert gutter(_render(named, thumb, [], [thumb])) > gutter(_render(unnamed, thumb, [], [thumb]))

    def header(image: QImage) -> int:
        return _label_ink_in_rect(image, _PAD + _ROW_LABEL_W, map_top, OVERLAY_WIDTH - _PAD, map_top + _COL_LABEL_H)

    two_cols = _render(_panel(current="c.mp4", seed_siblings=["s1"], action_siblings=[]), thumb, [thumb], [])
    one_col = _render(_panel(current="c.mp4", seed_siblings=[], action_siblings=[]), thumb, [], [])
    assert header(two_cols) > header(one_col)


def test_paint_hud_shows_a_lock_icon_on_the_current_clip_when_locked(qt_app):
    """A locked satellite paints a green padlock over the current clip; unlocked
    paints none, so the corner's top-left carries green ink only when locked."""
    thumb = _solid_pixmap(QColor(30, 30, 30))
    map_x = _PAD + _ROW_LABEL_W
    map_y = _PAD + _LOCK_BAND_H + _COL_LABEL_H  # no filter line on these panels

    def green_ink(image: QImage) -> int:
        total = 0
        for yy in range(map_y, map_y + 22):
            for xx in range(map_x, map_x + 22):
                c = image.pixelColor(xx, yy)
                if c.green() > c.red() + 40 and c.green() > c.blue() + 40:
                    total += 1
        return total

    locked = _render(_panel(locked=True, current="c.mp4", seed_siblings=[], action_siblings=[]), thumb, [], [])
    unlocked = _render(
        _panel(locked=False, lock_label="Unlocked", current="c.mp4", seed_siblings=[], action_siblings=[]),
        thumb, [], [],
    )
    assert green_ink(locked) > 0
    assert green_ink(unlocked) == 0


def test_sync_topmost_restakes_over_the_vlc_but_leaves_the_band_once_under_omnipause(qt_app):
    """Topmost is re-staked on every call, even when the overlay is already
    topmost: the satellite VLC it floats over is itself topmost and gets
    re-promoted above the HUD on mode switches and on resume from OmniPause,
    burying it — only an unconditional re-assert climbs back over, since a
    drift-corrected bit check cannot see "topmost but buried". The paused
    direction IS drift-corrected: leave the band once, don't churn it."""
    overlay = HudOverlay("portrait", lambda command: None)
    try:
        hwnd = int(overlay.winId())

        # Resume/normal, already topmost but buried under the re-promoted VLC →
        # re-stake anyway to climb back over it.
        with patch("fun_time.lock_hud_app.is_window_topmost", return_value=True), \
             patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.sync_topmost(desired_topmost=True)
        mock_set.assert_called_once_with(hwnd, True)

        # Just left OmniPause while non-topmost → restore topmost.
        with patch("fun_time.lock_hud_app.is_window_topmost", return_value=False), \
             patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.sync_topmost(desired_topmost=True)
        mock_set.assert_called_once_with(hwnd, True)

        # OmniPause while topmost → drop out of the band.
        with patch("fun_time.lock_hud_app.is_window_topmost", return_value=True), \
             patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.sync_topmost(desired_topmost=False)
        mock_set.assert_called_once_with(hwnd, False)

        # OmniPause and already out of the band → no churn.
        with patch("fun_time.lock_hud_app.is_window_topmost", return_value=False), \
             patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.sync_topmost(desired_topmost=False)
        mock_set.assert_not_called()
    finally:
        overlay.close()


def _hud_config(tmp_path) -> HudAppConfig:
    return HudAppConfig(
        layout=LayoutConfig(
            main_monitor=1, secondary_monitor=2,
            primary_top_ratio=0.7, landscape_width_ratio=0.66,
        ),
        portrait_port=8091, landscape_port=8092, vlc_password="",
        portrait_sources="C:/vids/p", landscape_sources="C:/vids/l",
        provider_media_root=None, provider_metadata_root=None,
        shared_state_file=tmp_path / "shared_bridge_state.ini",
        thumbnail_cache_dir=tmp_path / "thumbs",
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
    )


def _build_lock_hud(tmp_path) -> LockHud:
    """A LockHud with monitors stubbed and its constructor refresh suppressed,
    so a test can drive ``_apply`` without touching real monitors or VLC."""
    plan = SimpleNamespace(
        portrait=WindowRect(x=0, y=0, width=400, height=800),
        landscape=WindowRect(x=400, y=0, width=800, height=400),
    )
    corner = WindowRect(x=0, y=0, width=10, height=10)
    with patch("fun_time.lock_hud_app.enumerate_monitors", return_value=[]), \
         patch("fun_time.lock_hud_app.get_logical_monitor_rects", return_value=(corner, corner)), \
         patch("fun_time.lock_hud_app.compute_window_layout", return_value=plan), \
         patch.object(LockHud, "refresh"):
        return LockHud(_hud_config(tmp_path))


def test_apply_reloads_thumbnails_only_when_the_panel_changes(qt_app, tmp_path):
    """At 5 s clips the map has to flip the instant the clip changes, so the HUD
    polls fast — but a tick whose panel is unchanged must not reload thumbnails
    or repaint, or fast polling would burn the CPU it was meant to save.  It
    still re-stakes topmost every tick (that is what keeps it over the VLC)."""
    hud = _build_lock_hud(tmp_path)
    overlay = hud._overlays["portrait"]
    panel_a = _panel(current="C:/vids/a.mp4")
    panel_b = _panel(current="C:/vids/b.mp4")

    try:
        with patch("fun_time.lock_hud_app.thumbnail_for", return_value=None), \
             patch("fun_time.lock_hud_app.panel_thumbnails", return_value=[]), \
             patch.object(overlay, "sync_topmost") as mock_topmost, \
             patch.object(overlay, "set_content") as mock_set_content:
            hud._apply("portrait", panel_a, desired_topmost=True)   # first → build
            hud._apply("portrait", panel_a, desired_topmost=True)   # same → skip
            hud._apply("portrait", panel_b, desired_topmost=True)   # changed → build

        assert mock_set_content.call_count == 2, "rebuilt for A and B, skipped the repeat"
        assert mock_topmost.call_count == 3, "topmost is re-staked every tick regardless"
    finally:
        for ov in hud._overlays.values():
            ov.close()
