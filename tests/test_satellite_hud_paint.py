"""Pixel-level checks on the HUD bitmap mpv composites into the satellite video."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from satellite.hud import (
    ELLIPSIS_ROOM,
    LOCK_BAND_H,
    MAP_GAP,
    PAD,
    PANEL_SIZE,
    HudCell,
    HudModel,
    ellipsis_rects,
    looped_group_box,
)
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


def test_a_status_too_wide_for_the_panel_is_drawn_wrapped_not_clipped(thumb):
    """The real worst case on the narrow portrait panel: lock/loop, order, F-mode
    and a filter.  Pillow clips at the panel edge without a word, so an unwrapped
    line would silently drop the parts on the end — which reads as those states
    being off.  Wrapping is the only thing that gets them onto the screen.
    """
    label = "Looping actions · Latest · F-Mode · beta gamma"
    rgb = _rgb(HudRenderer("portrait").render(
        _model(lock_label=label, corner=HudCell(path="c.mp4", thumb=thumb))).bgra)
    width, _height = PANEL_SIZE["portrait"]

    # Text running into the panel's right margin is what clipping looks like: the
    # glyphs past it were simply never drawn.
    assert (rgb[:, width - PAD:] > 200).all(axis=2).sum() == 0


def test_a_wrapped_status_pushes_the_map_down_instead_of_overdrawing_it(thumb):
    """The map is laid out from the band's foot, so a second status line has to
    move it — otherwise the wrap lands on top of the corner thumbnail."""
    def corner_top(label: str) -> int:
        rendered = HudRenderer("portrait").render(
            _model(lock_label=label, corner=HudCell(path="c.mp4", thumb=thumb)))
        (_x, y, _w, _h), _path = rendered.targets.click[0]
        return y

    one_line = corner_top("Locked · Shuffle")
    wrapped = corner_top("Looping actions · Latest · F-Mode · beta gamma")

    assert wrapped > one_line


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


def _loop_model(thumb: str, playing, *, count: int = 12, loop: str = "seed") -> HudModel:
    """A seed row far longer than the map can draw, at *playing*."""
    return _model(
        locked=False, lock_label=f"Looping {count + 1} seeds" if loop else "Unlocked",
        corner=HudCell(path="c.mp4", thumb=thumb),
        seeds=tuple(HudCell(path=f"s{i}.mp4", thumb=thumb) for i in range(count)),
        active_loop=loop, playing=playing,
    )


def _tail_ink(rendered) -> int:
    """Ink in the slot kept past the right-hand end of the drawn row, inset from its
    edges so the loop rectangle's own border is never counted as a mark."""
    corner_rect = rendered.targets.click[0][0]
    seed_rects = [rect for rect, _p in rendered.targets.click[1:]]
    _before, after = ellipsis_rects(corner_rect, seed_rects, [], "seed")
    x, y, w, h = after
    return int((_rgb(rendered.bgra)[y + 2:y + h - 2, x + 2:x + w - 2] > 100).sum())


def test_a_map_with_more_clips_than_fit_says_so_even_off_a_loop(thumb):
    """The mark is about the map, not about looping: a browse row longer than the
    panel draws what fits and says there is more, rather than dropping the rest
    silently."""
    long_row = HudRenderer("portrait").render(_loop_model(thumb, ("corner", 0), loop=""))
    short_row = HudRenderer("portrait").render(_loop_model(thumb, ("corner", 0), count=1, loop=""))

    assert _tail_ink(long_row) > 0
    assert _tail_ink(short_row) == 0


def test_switching_a_loop_off_leaves_the_map_exactly_where_it_was(thumb):
    """The whole of what a loop toggle may change is the loop's own chrome.  Given the
    same cells, the drawn map — which clips, in which rects — is identical looping and
    not, so turning the loop off takes away the lit button and the rectangle and
    nothing else."""
    renderer = HudRenderer("portrait")
    looping = renderer.render(_loop_model(thumb, ("seed", 5)))
    ended = renderer.render(_loop_model(thumb, ("seed", 5), loop=""))

    assert looping.targets.click == ended.targets.click
    assert looping.targets.expand == ended.targets.expand
    assert [rect for rect, _kind in looping.targets.loop] == [rect for rect, _kind in ended.targets.loop]


def test_the_more_mark_reads_as_three_dots(thumb):
    """Fat dots at a tight spacing merged into one pill.  The mark has to read as
    three dots, so there are gaps between them."""
    rendered = HudRenderer("portrait").render(_loop_model(thumb, ("corner", 0)))
    corner_rect = rendered.targets.click[0][0]
    seed_rects = [rect for rect, _p in rendered.targets.click[1:]]
    _before, after = ellipsis_rects(corner_rect, seed_rects, [], "seed")
    x, y, w, h = after
    row = (_rgb(rendered.bgra)[y + h // 2, x:x + w] > 100).any(axis=1)

    runs = sum(1 for i, on in enumerate(row) if on and not (i and row[i - 1]))
    assert runs == 3


def test_the_more_mark_does_not_touch_the_loop_rectangle(thumb):
    """Dots drawn hard against the rectangle read as part of its border rather than
    as a mark inside it."""
    rendered = HudRenderer("portrait").render(_loop_model(thumb, ("seed", 5)))
    corner_rect = rendered.targets.click[0][0]
    seed_rects = [rect for rect, _p in rendered.targets.click[1:]]
    box = looped_group_box(corner_rect, seed_rects, [], "seed", reserve=ELLIPSIS_ROOM)
    before, _after = ellipsis_rects(corner_rect, seed_rects, [], "seed")

    assert before[0] - box[0] >= MAP_GAP
    bx, by, bw, bh = box
    # The strip just inside the rectangle's left border carries no ink at all.
    assert int((_rgb(rendered.bgra)[by + 2:by + bh - 2, bx + 2:bx + MAP_GAP] > 100).sum()) == 0


def test_a_long_loop_draws_a_window_that_holds_the_clip_on_screen(thumb):
    """The reported bug: a loop longer than the map could draw kept showing its
    first cells, so once it advanced past them nothing was lit and the clip playing
    was not among the thumbnails at all.  The map now follows the loop."""
    rendered = HudRenderer("portrait").render(_loop_model(thumb, ("seed", 8)))

    drawn = [path for _rect, path in rendered.targets.click]
    assert "s8.mp4" in drawn


def test_a_loop_just_started_draws_the_clip_on_screen_in_the_corner(thumb):
    """At the moment the loop starts, the clip on screen is its head — so it is the
    top-left cell, never somewhere in the middle of the row."""
    rendered = HudRenderer("portrait").render(_loop_model(thumb, ("corner", 0)))

    drawn = [path for _rect, path in rendered.targets.click]
    assert drawn[0] == "c.mp4"


def test_a_long_loop_lights_the_clip_on_screen_wherever_it_has_got_to(tmp_path: Path):
    """The window is only worth having if the highlight lands on the right cell in
    it: the clip playing is drawn bright and its neighbours dim."""
    bright = tmp_path / "bright.jpg"
    Image.new("RGB", (40, 60), (240, 240, 240)).save(bright)
    rendered = HudRenderer("portrait").render(_loop_model(str(bright), ("seed", 8)))

    by_path = {path: rect for rect, path in rendered.targets.click}

    def mean(rect):
        x, y, w, h = rect
        return float(_rgb(rendered.bgra)[y + 5:y + h - 5, x + 5:x + w - 5].mean())

    lit = mean(by_path["s8.mp4"])
    others = [mean(rect) for path, rect in by_path.items() if path != "s8.mp4"]
    assert others and lit > max(others)


def test_a_long_loop_marks_that_it_runs_on_past_the_map(thumb):
    """"…" at the end of the row says the loop holds more than is drawn — without it
    a three-cell map of a thirty-clip loop looks like the whole set."""
    renderer = HudRenderer("portrait")
    long_loop = renderer.render(_loop_model(thumb, ("corner", 0)))
    short_loop = renderer.render(_loop_model(thumb, ("corner", 0), count=1))

    assert _tail_ink(long_loop) > 0
    assert _tail_ink(short_loop) == 0


def test_a_sliding_loop_window_never_shifts_the_map(thumb):
    """The map must hold still as the window slides — the ellipses appearing and
    going is exactly when a shifting layout would be most distracting."""
    renderer = HudRenderer("portrait")
    at_start = renderer.render(_loop_model(thumb, ("corner", 0)))
    midway = renderer.render(_loop_model(thumb, ("seed", 6)))

    assert at_start.targets.click[0][0] == midway.targets.click[0][0]
    assert at_start.targets.loop == midway.targets.loop
    assert at_start.targets.expand == midway.targets.expand


def test_the_map_prints_how_big_each_axis_is(thumb):
    """The map draws only the cells that fit, so its top-left corner carries the
    counts — the only place the real size of each axis can be read.  They are always
    there, loop or no loop."""
    renderer = HudRenderer("portrait")

    def corner_ink(**counts) -> int:
        rendered = renderer.render(_model(corner=HudCell(path="c.mp4", thumb=thumb), **counts))
        (cx, cy, _cw, _ch), _path = rendered.targets.click[0]
        # The block left of the map and above its first row: the "Seed N" column
        # headers live to the right of it, over the thumbnails.
        block = _rgb(rendered.bgra)[PAD + LOCK_BAND_H:cy, PAD:cx - MAP_GAP]
        return int((block > 80).sum())

    assert corner_ink(seed_count=12, action_count=4) > 0
    assert corner_ink() == 0  # nothing to say before the index has answered


def test_the_filtered_actions_label_is_lit(thumb):
    """A filter shows on the map, on the row it holds you to — so which act you are
    filtered to is readable where you would act on it, and the lit label is the
    control that lifts it."""
    renderer = HudRenderer("portrait")

    def gutter_ink(filter_query: str) -> int:
        rendered = renderer.render(_model(
            corner=HudCell(path="c.mp4", thumb=thumb),
            actions=(HudCell(path="a1.mp4", thumb=thumb, label="gamma"),),
            current_action="alpha", filter_query=filter_query,
        ))
        (cx, cy, _cw, ch), _path = rendered.targets.click[0]
        # The corner's own row label, in the gutter beside it — "alpha".
        band = _rgb(rendered.bgra)[cy:cy + ch, PAD:cx - MAP_GAP]
        return int((band > 200).sum())  # near-white only; a plain label is grey

    assert gutter_ink("alpha") > 0
    assert gutter_ink("") == 0
    assert gutter_ink("gamma") == 0  # …that row's label lights, not this one


def test_gutter_width_fits_the_acts_present():
    """The gutter is sized to the acts actually shown — narrow for short ones, no
    wider than the cap for a long one — so it isn't a big empty margin."""
    from player_core.hud_panel import load_font

    from satellite.hud import MAX_GUTTER

    font = load_font(7)
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
    from player_core.hud_panel import load_font

    from satellite.hud_paint import _EXPAND_GLYPH, _LOOP_GLYPH, _SYMBOL_FONT

    glyph_font = load_font(11, _SYMBOL_FONT)
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
