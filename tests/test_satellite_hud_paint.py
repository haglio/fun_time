"""Pixel-level checks on the HUD bitmap mpv composites into the satellite video."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from satellite.hud import LOCK_BAND_H, PAD, PANEL_SIZE, HudCell, HudModel
from satellite.hud_paint import HudRenderer, gutter_width_for


@pytest.fixture
def thumb(tmp_path: Path) -> str:
    path = tmp_path / "thumb.jpg"
    Image.new("RGB", (40, 60), (30, 30, 30)).save(path)
    return str(path)


def _model(**overrides) -> HudModel:
    base = dict(side="portrait", locked=True, lock_label="Locked")
    base.update(overrides)
    return HudModel(**base)


def _rgb(bgra: np.ndarray) -> np.ndarray:
    """(H, W, 3) RGB view of an mpv BGRA buffer, for pixel assertions."""
    return bgra[:, :, [2, 1, 0]]


def test_render_fills_the_panel_and_draws_the_map(thumb):
    rendered = HudRenderer("portrait").render(
        _model(corner=HudCell(path="c.mp4", thumb=thumb),
               seeds=(HudCell(path="s1.mp4", thumb=thumb),),
               actions=(HudCell(path="a1.mp4", thumb=thumb, label="alpha"),))
    )

    width, height = PANEL_SIZE["portrait"]
    assert rendered.bgra.shape == (height, width, 4)
    assert (rendered.bgra[:, :, 3] > 0).mean() > 0.5


def test_render_rings_the_locked_clip_in_white(thumb):
    """The white ring marks a lock: a locked panel rings the corner, an unlocked
    one leaves no near-white ink on the map (below the lock band, where the
    "Locked" word can't be mistaken for the ring)."""
    def ring_ink(locked: bool) -> int:
        rendered = HudRenderer("portrait").render(
            _model(locked=locked, lock_label="Locked" if locked else "Unlocked",
                   corner=HudCell(path="c.mp4", thumb=thumb))
        )
        rgb = _rgb(rendered.bgra)[PAD + LOCK_BAND_H:, :]
        return int((rgb > 248).all(axis=2).sum())

    assert ring_ink(True) > 0
    assert ring_ink(False) == 0


def test_render_without_a_corner_still_draws_the_shell():
    """A satellite with no clip yet gets the lock band and nothing else — and no
    click targets, so a stray press over the empty panel posts nothing."""
    rendered = HudRenderer("landscape").render(
        HudModel(side="landscape", locked=False, lock_label="Unlocked"))

    assert (rendered.bgra[:, :, 3] > 0).any()
    assert rendered.targets.click == []
    assert rendered.targets.expand is None


def test_render_exposes_the_controls_it_drew(thumb):
    """Every drawn thumbnail, loop button and action label comes back as a hit
    target, so what is clickable is exactly what is visible."""
    rendered = HudRenderer("portrait").render(
        _model(corner=HudCell(path="c.mp4", thumb=thumb),
               seeds=(HudCell(path="s1.mp4", thumb=thumb),),
               actions=(HudCell(path="a1.mp4", thumb=thumb, label="gamma"),),
               current_action="alpha")
    )

    assert [path for _rect, path in rendered.targets.click] == ["c.mp4", "s1.mp4", "a1.mp4"]
    assert sorted(kind for _rect, kind in rendered.targets.loop) == ["action", "seed"]
    assert [name for _rect, name in rendered.targets.label] == ["alpha", "gamma"]
    assert rendered.targets.expand is not None


def test_the_playing_cell_is_brighter_than_the_others(tmp_path: Path):
    """The clip actually on screen is drawn at full opacity and the rest dim, so
    the bright one reads as "this is what's on" even mid-loop."""
    bright_thumb = tmp_path / "bright.jpg"
    Image.new("RGB", (40, 60), (240, 240, 240)).save(bright_thumb)
    cells = dict(
        corner=HudCell(path="c.mp4", thumb=str(bright_thumb)),
        seeds=(HudCell(path="s1.mp4", thumb=str(bright_thumb)),),
    )

    def corner_and_seed(playing) -> tuple[float, float]:
        rendered = HudRenderer("portrait").render(_model(playing=playing, **cells))
        corner_rect, seed_rect = rendered.targets.click[0][0], rendered.targets.click[1][0]

        def mean(rect):
            x, y, w, h = rect
            return float(_rgb(rendered.bgra)[y + 5:y + h - 5, x + 5:x + w - 5].mean())

        return mean(corner_rect), mean(seed_rect)

    corner_lit, seed_dim = corner_and_seed(("corner", 0))
    corner_dim, seed_lit = corner_and_seed(("seed", 0))
    assert corner_lit > corner_dim
    assert seed_lit > seed_dim


def test_gutter_width_fits_the_acts_present():
    """The gutter is sized to the acts actually shown — narrow for short ones, no
    wider than the cap for a long one — so it isn't a big empty margin."""
    from satellite.hud import MAX_GUTTER
    from satellite.hud_paint import _font

    font = _font(7)
    short = gutter_width_for(font, "Iota", ("Iota",))
    long = gutter_width_for(font, "Delta", ("Delta",))

    assert short < long <= MAX_GUTTER


def test_a_missing_thumbnail_still_draws_the_map():
    """A clip whose thumbnail fun_time hasn't produced yet gets a placeholder, so
    the map appears instantly instead of waiting on a frame grab."""
    rendered = HudRenderer("portrait").render(_model(corner=HudCell(path="c.mp4")))

    assert rendered.targets.click == [(rendered.targets.click[0][0], "c.mp4")]
    x, y, w, h = rendered.targets.click[0][0]
    assert (w, h) == (30, 54)


def test_hovering_a_button_draws_its_tooltip(thumb):
    """The tooltip is drawn into the panel — there is no native tooltip inside a
    video frame — so hovering adds ink the un-hovered render doesn't have."""
    renderer = HudRenderer("portrait")
    model = _model(corner=HudCell(path="c.mp4", thumb=thumb))

    plain = renderer.render(model)
    tipped = renderer.render(model, hover_loop="seed", hover_tip="Loop this seed row",
                             hover_pos=(40, 40))

    assert not np.array_equal(plain.bgra, tipped.bgra)


def test_the_button_glyphs_are_not_tofu():
    """Segoe UI has no U+21BB, so drawing the loop button with the UI face gives a
    ".notdef" box.  Qt fell back to Segoe UI Symbol silently; Pillow does not, so
    the glyph font must cover both button icons itself."""
    from satellite.hud_paint import _EXPAND_GLYPH, _LOOP_GLYPH, _SYMBOL_FONT, _font

    glyph_font = _font(11, _SYMBOL_FONT)
    notdef = glyph_font.getmask("").getbbox()

    assert glyph_font.getmask(_LOOP_GLYPH).getbbox() != notdef
    assert glyph_font.getmask(_EXPAND_GLYPH).getbbox() != notdef


def test_column_labels_are_clipped_to_their_column(thumb):
    """A portrait map's columns are barely wider than "Seed N", so a label must be
    cut at its column rather than run into the next one."""
    renderer = HudRenderer("portrait")
    rendered = renderer.render(
        _model(corner=HudCell(path="c.mp4", thumb=thumb),
               seeds=(HudCell(path="s1.mp4", thumb=thumb),))
    )

    (cx, _cy, cw, _ch), _path = rendered.targets.click[0]
    (sx, _sy, _sw, _sh), _seed = rendered.targets.click[1]
    # The header strip sits above the thumbnails; nothing may be drawn in the gap
    # between the corner column and the next one.
    header = _rgb(rendered.bgra)[PAD + LOCK_BAND_H:PAD + LOCK_BAND_H + 13, cx + cw:sx]
    assert (header > 60).sum() == 0
