from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from fun_time.command_dispatch import BridgeState
from fun_time.config import LayoutConfig
from fun_time.lock_hud import HudAppConfig, HudPanel
from fun_time.satellite_control import SatelliteStatus
from fun_time.lock_hud_app import (
    _COL_LABEL_H,
    _LOCK_BAND_H,
    _PAD,
    _ROW_LABEL_W,
    HudOverlay,
    LockHud,
    _friendly_action_label,
    build_click_targets,
    build_label_targets,
    hit_test_targets,
    hud_expand_button_rect,
    hud_loop_button_rects,
    hud_thumbnail_rects,
    paint_hud,
)
from fun_time.window_layout import WindowRect


# Canvas the pixel tests render a panel onto — independent of the live overlay
# sizes (OVERLAY_SIZE), which vary per side.
_CANVAS_W, _CANVAS_H = 320, 300


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
    image = QImage(_CANVAS_W, _CANVAS_H, QImage.Format.Format_ARGB32)
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

    total = (_CANVAS_W // 2) * (_CANVAS_H // 2)
    assert _samples(image, lambda c: c.alpha() > 0) > total * 0.5


def test_paint_hud_rings_the_locked_clip_in_white(qt_app):
    """The white ring now marks a lock, not the current clip: a locked panel
    rings the corner, an unlocked one leaves no near-white ink on the map (below
    the lock band, where the "Locked" word can't be mistaken for the ring)."""
    from fun_time.lock_hud_app import _LOCK_BAND_H, _PAD

    thumb = _solid_pixmap(QColor(30, 30, 30))
    map_top = _PAD + _LOCK_BAND_H

    def ring_ink(image: QImage) -> int:
        return sum(
            1
            for yy in range(map_top, image.height(), 2)
            for xx in range(0, image.width(), 2)
            if _is_near_white(image.pixelColor(xx, yy))
        )

    assert ring_ink(_render(_panel(locked=True), thumb, [], [])) > 0
    assert ring_ink(_render(_panel(locked=False, lock_label="Unlocked"), thumb, [], [])) == 0


def test_paint_hud_without_a_current_thumb_still_draws_its_shell(qt_app):
    image = _render(_panel(current="", seed_siblings=[], action_siblings=[]), None, [], [])

    assert _samples(image, lambda c: c.alpha() > 0) > 0


def test_hud_thumbnail_rects_positions_the_map_and_drops_overflow():
    """The corner anchors the map; seeds walk right and actions walk down, each
    dropped (not clipped) when it would cross the panel edge."""
    from fun_time.lock_hud_app import _MAP_GAP, _ROW_GAP

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
    assert actions == [(100, 50 + 54 + _ROW_GAP, 30, 54)]  # second dropped


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


def test_build_label_targets_maps_the_gutter_rows_to_actions():
    """Each row's action-name label is a gutter-wide target beside its thumbnail
    row: the corner's is the current action, the rows below their siblings."""
    corner = (60, 50, 30, 54)
    actions = [(60, 110, 30, 54)]

    targets = build_label_targets(
        corner, actions, gutter_x=10, gutter_w=50,
        current_action="Alpha", action_labels=["Gamma"],
    )

    assert targets == [((10, 50, 50, 54), "Alpha"), ((10, 110, 50, 54), "Gamma")]


def test_clicking_an_action_label_filters_to_that_action(qt_app):
    """A click on a row's action name posts filter_<side>_<action>, the same
    command speaking "[side] gamma" would."""
    sent: list[str] = []
    overlay = HudOverlay("portrait", sent.append)
    try:
        overlay._label_targets = [((0, 0, 50, 20), "Gamma")]
        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(5, 5)))
        assert sent == ["filter_portrait_gamma"]
    finally:
        overlay.close()


def test_single_click_switches_and_double_click_locks(qt_app):
    """A single click (deferred, then fired) posts play_video; a double-click
    cancels the pending single and posts lock_video; empty space posts nothing."""
    sent: list[str] = []
    overlay = HudOverlay("landscape", sent.append)
    try:
        overlay._click_targets = [((0, 0, 30, 30), "C:/vids/pick.mp4")]
        at_target = SimpleNamespace(position=lambda: QPointF(10, 10))

        # Single click → switch, once the double-click timer fires.
        overlay.mousePressEvent(at_target)
        overlay._fire_pending_click()
        assert sent == ["landscape_play_video|C:/vids/pick.mp4"]

        # Double click → lock, and the pending single is cancelled (no switch).
        sent.clear()
        overlay.mousePressEvent(at_target)
        overlay.mouseDoubleClickEvent(at_target)
        overlay._fire_pending_click()
        assert sent == ["landscape_lock_video|C:/vids/pick.mp4"]

        # Empty space → nothing.
        sent.clear()
        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(200, 200)))
        overlay._fire_pending_click()
        assert sent == []
    finally:
        overlay.close()


def test_hud_loop_button_rects_places_below_the_column_and_right_of_the_row():
    from fun_time.lock_hud_app import _LOOP_BTN, _MAP_GAP

    corner = (10, 10, 20, 20)
    loop_action, loop_seed = hud_loop_button_rects(
        corner, [(35, 10, 20, 20)], [(10, 35, 20, 20)], right=200, bottom=200,
    )

    assert loop_action == (10, 35 + 20 + _MAP_GAP, 20, _LOOP_BTN)   # below the lowest action
    assert loop_seed == (35 + 20 + _MAP_GAP, 10, _LOOP_BTN, 20)     # right of the rightmost seed

    # A panel too small for either drops it rather than overflowing.
    assert hud_loop_button_rects(corner, [(35, 10, 20, 20)], [(10, 35, 20, 20)], right=70, bottom=70) == (None, None)


def test_loop_buttons_toggle_and_are_mutually_exclusive(qt_app):
    """Clicking a loop button posts action_loop/seed_loop and marks it active;
    the other going on turns it off (they cannot coexist); clicking the active
    one again posts no_loop."""
    sent: list[str] = []
    overlay = HudOverlay("portrait", sent.append)
    try:
        overlay._loop_targets = [((0, 0, 20, 20), "action"), ((30, 0, 20, 20), "seed")]

        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(5, 5)))
        assert sent == ["portrait_action_loop"]
        assert overlay._active_loop == "action"

        sent.clear()
        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(35, 5)))
        assert sent == ["portrait_seed_loop"]
        assert overlay._active_loop == "seed"  # action turned off

        sent.clear()
        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(35, 5)))
        assert sent == ["portrait_no_loop"]
        assert overlay._active_loop == ""
    finally:
        overlay.close()


def test_hud_expand_button_sits_in_the_row_right_of_the_seed_loop_button():
    """The expand ("more seeds") button lives in the seed row, just right of the
    seed-loop button, and hides rather than overflow the panel's right edge."""
    from fun_time.lock_hud_app import _LOOP_BTN, _MAP_GAP

    loop_seed = (60, 10, 18, 30)  # right of the seed row

    assert hud_expand_button_rect(loop_seed, right=200) == (60 + 18 + _MAP_GAP, 10, _LOOP_BTN, 30)
    assert hud_expand_button_rect(None, right=200) is None
    # No horizontal room left → dropped, not spilled past the panel's right edge.
    assert hud_expand_button_rect(loop_seed, right=90) is None


def test_hud_button_tooltip_names_each_button():
    from fun_time.lock_hud_app import hud_button_tooltip

    loop_targets = [((0, 0, 20, 20), "action"), ((30, 0, 20, 20), "seed")]
    expand = (30, 30, 18, 18)

    assert hud_button_tooltip(loop_targets, expand, 5, 5) == "Loop this action column"
    assert hud_button_tooltip(loop_targets, expand, 35, 5) == "Loop this seed row"
    assert hud_button_tooltip(loop_targets, expand, 35, 35) == "More seeds — widen the net"
    assert hud_button_tooltip(loop_targets, expand, 200, 200) == ""


def test_hovering_a_button_arms_the_self_drawn_tooltip(qt_app):
    """The overlay tracks the tooltip text for the button under the cursor and
    draws it itself (a native tooltip falls behind this topmost window); it
    clears off the buttons."""
    overlay = HudOverlay("portrait", lambda command: None)
    try:
        overlay._loop_targets = [((0, 0, 20, 20), "seed")]
        overlay._expand_rect = None

        overlay.mouseMoveEvent(SimpleNamespace(position=lambda: QPointF(5, 5)))
        assert overlay._hover_tip == "Loop this seed row"

        overlay.mouseMoveEvent(SimpleNamespace(position=lambda: QPointF(200, 200)))
        assert overlay._hover_tip == ""
    finally:
        overlay.close()


def test_clicking_the_expand_button_posts_more_seeds(qt_app):
    """The expand button widens the net — the click posts "<side>_more_seeds"."""
    sent: list[str] = []
    overlay = HudOverlay("landscape", sent.append)
    try:
        overlay._expand_rect = (0, 0, 18, 18)
        overlay.mousePressEvent(SimpleNamespace(position=lambda: QPointF(5, 5)))
        assert sent == ["landscape_more_seeds"]
    finally:
        overlay.close()


def test_hovering_a_loop_button_marks_the_preview_axis(qt_app):
    overlay = HudOverlay("portrait", lambda command: None)
    try:
        overlay._loop_targets = [((0, 0, 20, 20), "action")]

        overlay.mouseMoveEvent(SimpleNamespace(position=lambda: QPointF(5, 5)))
        assert overlay._hover_loop == "action"

        overlay.mouseMoveEvent(SimpleNamespace(position=lambda: QPointF(100, 100)))
        assert overlay._hover_loop == ""
    finally:
        overlay.close()


def test_friendly_action_label_titlecases_and_keeps_acronyms_upper():
    assert _friendly_action_label("epsilon") == "Epsilon"
    assert _friendly_action_label("cowsubject") == "Cowsubject"
    # Each word wraps to its own line, with acronyms kept upper.
    assert _friendly_action_label("pov gamma") == "POV\nGamma"
    assert _friendly_action_label("reverse cowsubject") == "Reverse\nCowsubject"


def test_gutter_width_fits_the_acts_present(qt_app):
    """The gutter is sized to the acts actually shown — narrow for short ones, no
    wider than the cap for a long one — so it isn't a big empty margin."""
    from fun_time.lock_hud_app import gutter_width_for, _MAX_GUTTER

    short = gutter_width_for("Iota", ("Iota",))
    long = gutter_width_for("Delta", ("Delta",))

    assert short < long <= _MAX_GUTTER


def test_friendly_action_label_keeps_a_single_word_whole():
    # A long single word stays on one line (the gutter/font are sized to fit it),
    # rather than being split at an unnatural point.
    assert _friendly_action_label("delta") == "Delta"
    assert _friendly_action_label("delta") == "Delta"


def test_action_label_blocks_separate_comma_joined_acts():
    """Several acts on one clip ("Alpha, Theta Motion") become one block each
    (drawn with a gap between), commas dropped; one act is a single block."""
    from fun_time.lock_hud_app import _action_label_blocks

    assert _action_label_blocks("alpha, theta motion") == [["Alpha"], ["Motion", "Bounce"]]
    assert _action_label_blocks("pov gamma") == [["POV", "Gamma"]]
    assert _action_label_blocks("") == [["(unknown)"]]


def test_friendly_action_label_shows_unknown_for_missing_metadata():
    assert _friendly_action_label("") == "(unknown)"
    assert _friendly_action_label("   ") == "(unknown)"


def _loop_panel(active_loop: str) -> HudPanel:
    return HudPanel(
        side="portrait", locked=False, lock_label="", current="C:/v/a.mp4",
        seed_siblings=[], action_siblings=[], active_loop=active_loop, playing="C:/v/a.mp4",
    )


def test_set_content_keeps_the_loop_lit_from_the_panel(qt_app):
    """The loop's lit state is authoritative from the shared state (carried on the
    panel), so it survives the clip auto-advancing within the loop and clears only
    when the state says the loop ended."""
    overlay = HudOverlay("portrait", lambda command: None)
    try:
        overlay.set_content(_loop_panel("seed"), None, [], [], [], [])
        assert overlay._active_loop == "seed"

        overlay.set_content(_loop_panel(""), None, [], [], [], [])
        assert overlay._active_loop == ""
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
    no_map = _panel(current="c.mp4", seed_siblings=[], action_siblings=["a1"],
                    current_action="Alpha", action_labels=("Delta",))

    def gutter(image: QImage) -> int:
        return _label_ink_in_rect(image, _PAD, map_top, _PAD + _ROW_LABEL_W, _CANVAS_H)

    # Naming the rows fills the gutter with action ink; with no map drawn (no
    # current thumbnail) there is no gutter at all.
    assert gutter(_render(named, thumb, [], [thumb])) > gutter(_render(no_map, None, [], []))

    def header(image: QImage) -> int:
        return _label_ink_in_rect(image, _PAD + _ROW_LABEL_W, map_top, _CANVAS_W - _PAD, map_top + _COL_LABEL_H)

    two_cols = _render(_panel(current="c.mp4", seed_siblings=["s1"], action_siblings=[]), thumb, [thumb], [])
    one_col = _render(_panel(current="c.mp4", seed_siblings=[], action_siblings=[]), thumb, [], [])
    assert header(two_cols) > header(one_col)


def test_paint_hud_draws_unknown_for_a_row_missing_its_action(qt_app):
    """A clip with no action metadata still gets a labelled row — "(unknown)" —
    rather than an invisible, blank gutter."""
    thumb = _solid_pixmap(QColor(30, 30, 30))
    map_top = _PAD + _LOCK_BAND_H
    unnamed = _panel(current="c.mp4", seed_siblings=[], action_siblings=["a1"],
                     current_action="", action_labels=("",))

    ink = _label_ink_in_rect(_render(unnamed, thumb, [], [thumb]), _PAD, map_top,
                             _PAD + _ROW_LABEL_W, _CANVAS_H)
    assert ink > 0


def test_stack_above_matches_the_overlays_band_to_its_satellite(qt_app):
    """The overlay re-stakes into whatever band its satellite is in — topmost while
    the satellite is topmost (play), non-topmost when OmniPause drops it — landing
    at the top of that band, above its own video either way (never tracking
    OmniPause itself, just its satellite)."""
    overlay = HudOverlay("portrait", lambda command: None)
    try:
        hwnd = int(overlay.winId())
        with patch("fun_time.lock_hud_app.is_window_topmost", return_value=True) as mock_top, \
             patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.stack_above(4242)
        mock_top.assert_called_once_with(4242)          # reads the satellite's band
        mock_set.assert_called_once_with(hwnd, True)    # matches its own to it
        with patch("fun_time.lock_hud_app.is_window_topmost", return_value=False), \
             patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.stack_above(4242)                    # satellite dropped (OmniPause)
        mock_set.assert_called_once_with(hwnd, False)   # HUD drops with it
    finally:
        overlay.close()


def test_stack_above_does_nothing_without_a_resolved_satellite(qt_app):
    """No satellite window yet (hwnd 0) → skip this tick and try again next, rather
    than banding against a bogus window."""
    overlay = HudOverlay("portrait", lambda command: None)
    try:
        with patch("fun_time.lock_hud_app.set_always_on_top") as mock_set:
            overlay.stack_above(0)
        mock_set.assert_not_called()
    finally:
        overlay.close()


def test_satellite_hwnd_resolves_from_the_pid_and_caches(qt_app, tmp_path):
    """The satellite window is found from its recorded PID once and cached, so the
    fast refresh doesn't re-enumerate every tick."""
    (tmp_path / "bridge_pids.ini").write_text(
        "[pids]\nportrait_pid = 300\nlandscape_pid = 400\n", encoding="utf-8")
    hud = _build_lock_hud(tmp_path)
    try:
        with patch("fun_time.lock_hud_app.find_window_by_pid", return_value=7777) as mock_find, \
             patch("fun_time.lock_hud_app.is_window", return_value=True):
            assert hud._satellite_hwnd("portrait") == 7777
            assert hud._satellite_hwnd("portrait") == 7777  # cached
        mock_find.assert_called_once_with(300)
    finally:
        for ov in hud._overlays.values():
            ov.close()


def test_satellite_hwnd_reresolves_a_stale_handle(qt_app, tmp_path):
    """A cached handle that no longer names a live window (a satellite restart) is
    re-resolved rather than clung to."""
    (tmp_path / "bridge_pids.ini").write_text(
        "[pids]\nportrait_pid = 300\n", encoding="utf-8")
    hud = _build_lock_hud(tmp_path)
    hud._satellite_hwnds["portrait"] = 1111  # a now-dead handle
    try:
        with patch("fun_time.lock_hud_app.is_window", return_value=False), \
             patch("fun_time.lock_hud_app.find_window_by_pid", return_value=2222) as mock_find:
            assert hud._satellite_hwnd("portrait") == 2222
        mock_find.assert_called_once_with(300)
    finally:
        for ov in hud._overlays.values():
            ov.close()


def _hud_config(tmp_path) -> HudAppConfig:
    return HudAppConfig(
        layout=LayoutConfig(
            main_monitor=1, secondary_monitor=2,
            primary_top_ratio=0.7, landscape_width_ratio=0.66,
        ),
        portrait_status_file=tmp_path / "portrait_status.txt",
        landscape_status_file=tmp_path / "landscape_status.txt",
        portrait_sources="C:/vids/p", landscape_sources="C:/vids/l",
        provider_media_root=None, provider_metadata_root=None,
        shared_state_file=tmp_path / "shared_bridge_state.ini",
        thumbnail_cache_dir=tmp_path / "thumbs",
        dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
        ready_file=tmp_path / "lock_hud_ready.txt",
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


def test_constructing_the_hud_signals_ready(qt_app, tmp_path):
    """The HUD touches its ready flag once built (indexes primed), so startup can
    drop the loading screen knowing the maps will paint immediately."""
    assert not (tmp_path / "lock_hud_ready.txt").exists()

    _build_lock_hud(tmp_path)

    assert (tmp_path / "lock_hud_ready.txt").exists()


def test_apply_reloads_thumbnails_only_when_the_panel_changes(qt_app, tmp_path):
    """At 5 s clips the map has to flip the instant the clip changes, so the HUD
    polls fast — but a tick whose panel is unchanged must not reload thumbnails
    or repaint, or fast polling would burn the CPU it was meant to save.  It
    still re-stacks above its satellite every tick (that is what keeps it over
    the VLC)."""
    hud = _build_lock_hud(tmp_path)
    overlay = hud._overlays["portrait"]
    panel_a = _panel(current="C:/vids/a.mp4", seed_siblings=[], action_siblings=[])
    panel_b = _panel(current="C:/vids/b.mp4", seed_siblings=[], action_siblings=[])

    loaded = _solid_pixmap(QColor(1, 1, 1))
    try:
        # Corner thumbnail resolves (cached), so the panel is fully loaded and an
        # unchanged tick is not pending — the skip can happen.
        with patch("fun_time.lock_hud_app.cached_thumbnail", return_value=tmp_path / "x.jpg"), \
             patch("fun_time.lock_hud_app._load_pixmap", return_value=loaded), \
             patch("fun_time.lock_hud_app.panel_thumbnails", return_value=[]), \
             patch.object(overlay, "stack_above") as mock_stack, \
             patch.object(overlay, "set_content") as mock_set_content:
            hud._apply("portrait", panel_a)   # first → build
            hud._apply("portrait", panel_a)   # same, fully loaded → skip
            hud._apply("portrait", panel_b)   # changed → build

        assert mock_set_content.call_count == 2, "rebuilt for A and B, skipped the repeat"
        assert mock_stack.call_count == 3, "re-stacked above its satellite every tick regardless"
    finally:
        for ov in hud._overlays.values():
            ov.close()


def test_apply_reloads_an_unchanged_panel_until_its_thumbnails_cache(qt_app, tmp_path):
    """A not-yet-cached thumbnail must not freeze the map on a placeholder: while
    a side is still missing thumbnails the HUD reloads each tick (even with the
    same panel) so they fill in without waiting for the clip to change."""
    hud = _build_lock_hud(tmp_path)
    overlay = hud._overlays["portrait"]
    panel = _panel(current="C:/vids/a.mp4")

    try:
        with patch("fun_time.lock_hud_app.cached_thumbnail", return_value=None), \
             patch("fun_time.lock_hud_app.panel_thumbnails", return_value=[]), \
             patch.object(overlay, "stack_above"), \
             patch.object(overlay, "set_content") as mock_set_content:
            hud._apply("portrait", panel)   # corner thumb missing → pending
            hud._apply("portrait", panel)   # same panel, still pending → reload

        assert mock_set_content.call_count == 2
    finally:
        for ov in hud._overlays.values():
            ov.close()


def test_refresh_stacks_each_overlay_above_its_own_satellite(qt_app, tmp_path):
    """A full refresh resolves each satellite from its recorded PID and stacks that
    side's overlay directly above it — the mechanism that keeps each map on its own
    video and following the satellite's band."""
    (tmp_path / "bridge_pids.ini").write_text(
        "[pids]\nportrait_pid = 300\nlandscape_pid = 400\n", encoding="utf-8")
    hud = _build_lock_hud(tmp_path)
    panel = _panel(current="C:/vids/a.mp4", seed_siblings=[], action_siblings=[])

    def fake_find(pid):
        return {300: 31, 400: 41}.get(pid, 0)

    try:
        with patch("fun_time.lock_hud_app.read_shared_state", return_value=BridgeState()), \
             patch("fun_time.lock_hud_app.loading_screen_active", return_value=False), \
             patch("fun_time.lock_hud_app.read_satellite_status", return_value=SatelliteStatus()), \
             patch("fun_time.lock_hud_app.build_panels", return_value=(panel, panel)), \
             patch("fun_time.lock_hud_app.cached_thumbnail", return_value=None), \
             patch("fun_time.lock_hud_app.panel_thumbnails", return_value=[]), \
             patch("fun_time.lock_hud_app.find_window_by_pid", side_effect=fake_find), \
             patch("fun_time.lock_hud_app.is_window", return_value=True), \
             patch.object(hud._overlays["portrait"], "stack_above") as p_stack, \
             patch.object(hud._overlays["landscape"], "stack_above") as l_stack:
            hud.refresh()
        p_stack.assert_called_once_with(31)
        l_stack.assert_called_once_with(41)
    finally:
        for ov in hud._overlays.values():
            ov.close()
