"""Pixel-level checks on the HUD bitmap mpv composites into the satellite video."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from player_core.hud_panel import ICON_GRIDS, TEXT_MUTED, WHITE

from satellite.hud import (
    COL_LABEL_H,
    CONTROL_TOOLTIPS,
    CTRL_BAND_H,
    ELLIPSIS_ROOM,
    FILTER_ROOM,
    MAP_CELLS,
    MAP_GAP,
    PAD,
    STATUS_BAND_H,
    STATUS_DOT,
    STATUS_TEXT_X,
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


# A side's clips are NOT all one shape, which is the whole reason the panel is
# measured around the map instead of the map fitted into the panel.  fun_time
# caches thumbnails at a 160px longest edge, so these are the shapes the map scales
# down: each side's nominal one (9:16, 16:9) and a squarer one, which is the case
# that used to lose a cell.
CLIP_SHAPES = {
    "portrait": [(90, 160), (132, 160)],
    "landscape": [(160, 90), (160, 125)],
}


@pytest.fixture
def clip_thumb(tmp_path: Path):
    """``(side, shape) -> thumbnail path`` for the shapes above."""
    def make(side: str, shape: tuple[int, int]) -> str:
        path = tmp_path / f"{side}-{shape[0]}x{shape[1]}.jpg"
        if not path.exists():
            Image.new("RGB", shape, (30, 30, 30)).save(path)
        return str(path)
    return make


def _sides_and_shapes() -> list[tuple[str, tuple[int, int]]]:
    return [(side, shape) for side, shapes in CLIP_SHAPES.items() for shape in shapes]


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

    assert (rendered.bgra[:, :, 3] > 0).mean() > 0.5


def _crowded(side: str, thumb: str) -> HudModel:
    """A side with more seeds and more acts than any map draws."""
    return HudModel(
        side=side, lock_label="Unlocked · Shuffle", current_action="Alpha",
        corner=HudCell(path="c.mp4", thumb=thumb),
        seeds=tuple(HudCell(path=f"s{i}.mp4", thumb=thumb) for i in range(6)),
        actions=tuple(HudCell(path=f"a{i}.mp4", thumb=thumb, label="Alpha") for i in range(4)),
        seed_count=7, action_count=5,
    )


@pytest.mark.parametrize("side,shape", _sides_and_shapes())
def test_the_map_is_three_cells_a_side_whatever_shape_its_clips_are(side, shape, clip_thumb):
    """The reported bug: the portrait map drew two cells across where the landscape
    one drew three.

    The panel used to be measured against one assumed cell width per side — the
    narrowest a portrait clip gets — and the map then windowed into whatever room
    that left, so a row of the wider portrait clips ran out of panel after the second
    one.  The count is fixed now and the panel gives, so every shape gets three.
    """
    rendered = HudRenderer(side).render(_crowded(side, clip_thumb(side, shape)))

    rects = [rect for rect, _path in rendered.targets.click]
    columns = {x for x, _y, _w, _h in rects}
    rows = {y for _x, y, _w, _h in rects}
    assert (len(columns), len(rows)) == (MAP_CELLS, MAP_CELLS)


@pytest.mark.parametrize("side,shape", _sides_and_shapes())
def test_the_panel_stops_where_its_last_controls_do(side, shape, clip_thumb):
    """No slab past the map wherever the map is what set the width: the expand
    button ends one margin in from the right edge and the action-loop button one
    margin up from the bottom, so every pixel of the panel is carrying something —
    for a wide cell as much as a narrow one.  (A status longer than the map is the
    other case, and there it is the line that reaches the far edge.)"""
    rendered = HudRenderer(side).render(_crowded(side, clip_thumb(side, shape)))
    height, width = rendered.bgra.shape[:2]

    ex, _ey, ew, _eh = rendered.targets.expand
    loop = dict((kind, rect) for rect, kind in rendered.targets.loop)
    _lx, ly, _lw, lh = loop["action"]
    assert ex + ew == width - PAD
    assert ly + lh == height - PAD


def test_a_satellite_with_no_clip_yet_gets_a_panel_only_as_tall_as_its_bands(thumb):
    """Before the first clip there is no map, so the panel is the status and control
    bands and nothing else — it grows to the map when there is one to draw."""
    renderer = HudRenderer("portrait")
    shell = renderer.render(HudModel(side="portrait", lock_label="Unlocked"))
    mapped = renderer.render(_model(corner=HudCell(path="c.mp4", thumb=thumb)))

    assert shell.bgra.shape[0] < mapped.bgra.shape[0]


def test_render_rings_the_locked_clip_in_white(thumb):
    """The white ring marks a lock: a locked panel rings the corner, an unlocked
    one leaves no near-white ink on the map (below the lock band, where the
    "Locked" word can't be mistaken for the ring)."""
    def ring_ink(locked: bool) -> int:
        rendered = HudRenderer("portrait").render(
            _model(locked=locked, lock_label="Locked" if locked else "Unlocked",
                   corner=HudCell(path="c.mp4", thumb=thumb))
        )
        rgb = _rgb(rendered.bgra)[PAD + STATUS_BAND_H:, :]
        return int((rgb > 248).all(axis=2).sum())

    assert ring_ink(True) > 0
    assert ring_ink(False) == 0


def test_the_lock_ring_follows_the_clip_being_held(thumb):
    """A lock taken inside a loop holds whichever member the loop had reached, and
    the map stays anchored where the loop started — so the ring has to land on the
    cell that clip is drawn in.  Left on the corner it rings a clip that is neither
    playing nor locked."""
    def ring_ink(playing) -> tuple[int, int]:
        rendered = HudRenderer("portrait").render(
            _model(playing=playing, active_loop="seed",
                   lock_label="Looping seeds · Locked · Shuffle",
                   corner=HudCell(path="c.mp4", thumb=thumb),
                   seeds=(HudCell(path="s1.mp4", thumb=thumb),)))
        rgb = _rgb(rendered.bgra)

        def white_in(rect) -> int:
            x, y, w, h = rect
            return int((rgb[y:y + h, x:x + w] > 248).all(axis=2).sum())

        corner_rect, seed_rect = rendered.targets.click[0][0], rendered.targets.click[1][0]
        return white_in(corner_rect), white_in(seed_rect)

    corner_ringed, seed_bare = ring_ink(("corner", 0))
    corner_bare, seed_ringed = ring_ink(("seed", 0))
    assert corner_ringed > corner_bare
    assert seed_ringed > seed_bare


def test_render_without_a_corner_still_draws_the_shell():
    """A satellite with no clip yet gets the lock band and nothing else — and no
    click targets, so a stray press over the empty panel posts nothing."""
    rendered = HudRenderer("landscape").render(
        HudModel(side="landscape", locked=False, lock_label="Unlocked"))

    assert (rendered.bgra[:, :, 3] > 0).any()
    assert rendered.targets.click == []
    assert rendered.targets.expand is None


def test_the_dot_lights_up_only_on_the_active_side(thumb):
    """The dot beside the status line says whether a bare "lock" or "next" would
    land here.  Lit is white, idle is the palette's gray — never absent, because a
    missing dot could not be told from an idle one, and then the lit one on the
    other player would be the only readable state."""
    def dot(active: bool) -> np.ndarray:
        rendered = HudRenderer("portrait").render(
            _model(active=active, lock_label="Unlocked · Shuffle",
                   corner=HudCell(path="c.mp4", thumb=thumb)))
        # The dot's own square, left of where the status text starts.
        return _rgb(rendered.bgra)[PAD + 2:PAD + 12, PAD:PAD + 10]

    assert np.allclose(dot(True).reshape(-1, 3).mean(axis=0), WHITE, atol=40)
    assert np.allclose(dot(False).reshape(-1, 3).mean(axis=0), TEXT_MUTED, atol=40)


def test_the_status_text_starts_clear_of_the_dot(thumb):
    """Drawn over each other they would be illegible, and the width the line asks
    the panel for is measured from the room the dot leaves, not from the far edge."""
    rendered = HudRenderer("portrait").render(
        _model(active=True, lock_label="Unlocked", corner=HudCell(path="c.mp4", thumb=thumb)))
    rgb = _rgb(rendered.bgra)

    # STATUS_TEXT_X is absolute, so the gap runs from the dot's right edge to it —
    # skipping the 2px where the round dot's antialiased edge feathers out, which
    # is the dot, not text starting early.
    gap = rgb[PAD:PAD + 14, PAD + STATUS_DOT + 2:STATUS_TEXT_X]
    assert (gap > 200).all(axis=2).sum() == 0, "text ink in the gap before the text starts"


def test_a_status_too_wide_for_the_map_widens_the_panel_rather_than_wrapping(thumb):
    """The real worst case on the narrow portrait panel: lock/loop, order, F-mode
    and a filter.  The panel is measured around the map, and three of those parts
    already outrun what a column of portrait clips is wide — so the width the map
    asks for is a floor, not the answer, and the status takes whatever more it
    needs.  A second line would be the alternative, and the line reads as one
    state of one side; split across two it reads as two."""
    def rendered(label: str):
        return HudRenderer("portrait").render(
            _model(lock_label=label, corner=HudCell(path="c.mp4", thumb=thumb)))

    short = rendered("Locked")
    long = rendered("Looping actions · Latest · F-Mode · beta gamma")

    assert long.bgra.shape[1] > short.bgra.shape[1]
    assert long.bgra.shape[0] == short.bgra.shape[0]


def _control_band_top(rendered) -> int:
    """Where the side's own control buttons start — the row under the status block,
    and so how deep that block ended up."""
    return min(y for (_x, y, _w, _h), _name in rendered.targets.control)


def test_the_file_on_screen_is_named_under_the_status_line(thumb):
    """The main player names its file in a muted line under its status, and the
    satellites lead with the same block — so "what is this clip?" is answered in the
    same corner of every player rather than only on the main one."""
    renderer = HudRenderer("portrait")
    model = _model(lock_label="Unlocked", corner=HudCell(path="c.mp4", thumb=thumb))
    bare = renderer.render(model)
    named = renderer.render(model, video="example clip one")

    added = named.bgra.shape[0] - bare.bgra.shape[0]
    # A second line, not more of the first: the panel gains exactly the line it drew
    # and everything under it moves down by that much.
    assert added > 0
    assert _control_band_top(named) - _control_band_top(bare) == added
    # …and the name is in the room it added, in the muted gray, not the status line's
    # full-strength white.
    strip = _rgb(named.bgra)[_control_band_top(bare):_control_band_top(named)]
    assert (strip > 80).any(axis=2).sum() > 0
    assert (strip > 200).all(axis=2).sum() == 0


def test_a_file_name_too_wide_for_the_map_widens_the_panel(thumb):
    """Names run long, and a name cut off mid-word says nothing about which clip this
    is — which is the whole reason it is drawn.  So it takes the width it needs, the
    way the status line above it does."""
    renderer = HudRenderer("portrait")
    model = _model(lock_label="Unlocked", corner=HudCell(path="c.mp4", thumb=thumb))

    short = renderer.render(model, video="clip")
    long = renderer.render(
        model, video="a considerably longer example clip name than the map is wide")

    assert long.bgra.shape[1] > short.bgra.shape[1]
    assert long.bgra.shape[0] == short.bgra.shape[0]


def test_the_map_sits_where_it_sits_however_long_the_status_is(thumb):
    """The status band is one line deep and stays one line deep, so the map is
    anchored at the same place on every panel — a side that has picked up a filter
    does not find its map a line lower than the side that has not."""
    def corner(label: str):
        rendered = HudRenderer("portrait").render(
            _model(lock_label=label, corner=HudCell(path="c.mp4", thumb=thumb)))
        (x, y, _w, _h), _path = rendered.targets.click[0]
        return x, y

    assert (corner("Looping actions · Latest · F-Mode · beta gamma")
            == corner("Locked · Shuffle") == corner(""))


def test_render_exposes_the_controls_it_drew(thumb):
    """Every drawn thumbnail, loop button and filter button comes back as a hit
    target, so what is clickable is exactly what is visible."""
    rendered = HudRenderer("portrait").render(
        _model(corner=HudCell(path="c.mp4", thumb=thumb),
               seeds=(HudCell(path="s1.mp4", thumb=thumb),),
               actions=(HudCell(path="a1.mp4", thumb=thumb, label="gamma"),),
               current_action="alpha")
    )

    assert [path for _rect, path in rendered.targets.click] == ["c.mp4", "s1.mp4", "a1.mp4"]
    assert sorted(kind for _rect, kind in rendered.targets.loop) == ["action", "seed"]
    assert [name for _rect, name in rendered.targets.filter] == ["alpha", "gamma"]
    assert rendered.targets.expand is not None


def test_render_draws_the_sides_own_controls_even_with_no_clip():
    """The buttons the dashboard used to carry are the side's, not the map's, so
    they are there before the first clip arrives — a satellite that came up empty
    can still be stepped off it, and still narrowed to its favorites."""
    rendered = HudRenderer("landscape").render(
        HudModel(side="landscape", locked=False, lock_label="Unlocked"))

    assert [name for _rect, name in rendered.targets.control] == [
        "prev", "next", "lock", "trash", "fmode", "reset", "minimize",
    ]
    assert rendered.targets.favorite is not None


def test_the_minimize_button_wears_a_bar_rather_than_a_font_glyph():
    """The minimize mark Windows uses lives in a face these buttons don't load, and
    Pillow draws a ".notdef" box for a codepoint a face doesn't carry — so it is
    drawn: one horizontal run of ink across the middle of the button, wider than it
    is tall, which is what a title bar's minimize looks like everywhere."""
    rendered = HudRenderer("landscape").render(
        HudModel(side="landscape", lock_label="Unlocked"))
    x, y, w, h = {name: rect for rect, name in rendered.targets.control}["minimize"]
    # The button's own outline is its border, so only the interior is the mark.
    inside = _rgb(rendered.bgra)[y + 2:y + h - 2, x + 2:x + w - 2]
    ys, xs = np.nonzero((inside > 60).all(axis=2))

    assert len(ys), "the minimize button drew no mark at all"
    assert xs.max() - xs.min() > ys.max() - ys.min()  # a bar, not a box or a glyph


def test_the_state_controls_and_favorite_mark_light_up_when_they_apply():
    """Green is what the dashboard's panel used, so everything that is a *state*
    keeps it: the lock button while the side is locked, the F button while the
    side is in F-mode, the star while the clip is a favorite."""
    def ink(rect, rendered) -> int:
        x, y, w, h = rect
        rgb = _rgb(rendered.bgra)[y:y + h, x:x + w].astype(int)
        green = (rgb[:, :, 1] > 100) & (rgb[:, :, 0] < 100) & (rgb[:, :, 2] < 100)
        return int(green.sum())

    def rendered_with(**overrides):
        return HudRenderer("landscape").render(
            HudModel(side="landscape", lock_label="Unlocked", **overrides))

    off = rendered_with()
    on = rendered_with(locked=True, is_favorite=True, f_mode=True)
    rects = {name: rect for rect, name in on.targets.control}

    assert ink(rects["lock"], on) > ink(rects["lock"], off)
    assert ink(rects["fmode"], on) > ink(rects["fmode"], off)
    assert ink(on.targets.favorite, on) > ink(off.targets.favorite, off)


def test_f_mode_wears_its_own_badge_rather_than_a_typed_letter():
    """`fmode_icon.ico` is a pink five-by-five "F" — the mark the mode has on the
    taskbar and on the main console — and a letter set in the body face is a
    thin thing beside it.  The mark holds whether or not the mode is on; only what
    is behind it changes."""
    for f_mode in (False, True):
        rendered = HudRenderer("landscape").render(
            HudModel(side="landscape", lock_label="Unlocked", f_mode=f_mode))
        x, y, w, h = {name: rect for rect, name in rendered.targets.control}["fmode"]
        box = _rgb(rendered.bgra)[y:y + h, x:x + w]
        pink = (box == np.array((200, 80, 160), dtype=box.dtype)).all(axis=2)
        ys, xs = np.nonzero(pink)
        cell = (xs.max() - xs.min() + 1) / 5
        drawn = [
            "".join("#" if pink[int(ys.min() + (r + 0.5) * cell),
                                int(xs.min() + (c + 0.5) * cell)] else "."
                    for c in range(5))
            for r in range(5)
        ]

        assert drawn == list(ICON_GRIDS["F"]), f_mode


def test_a_running_loops_button_fills_white_and_not_the_locks_green(thumb):
    """The lock and the star are the panel's two favorites marks, and green is
    theirs alone; a loop is just this side repeating a group, so its button lights
    in the plain white every other control here uses."""
    rendered = HudRenderer("portrait").render(_loop_model(thumb, ("corner", 0)))
    x, y, w, h = dict((kind, rect) for rect, kind in rendered.targets.loop)["seed"]
    rgb = _rgb(rendered.bgra)[y:y + h, x:x + w].astype(int)

    assert (rgb > 240).all(axis=2).any()
    green = (rgb[:, :, 1] > 100) & (rgb[:, :, 0] < 100) & (rgb[:, :, 2] < 100)
    assert not green.any()


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
    going is exactly when a shifting layout would be most distracting.  Two
    mid-loop frames are the like-for-like pair: the one deliberate move — the
    action column stepping out to hang under the lit cell — happens as the loop
    leaves its head, not per advance."""
    renderer = HudRenderer("portrait")
    at_start = renderer.render(_loop_model(thumb, ("corner", 0)))
    midway = renderer.render(_loop_model(thumb, ("seed", 5)))
    later = renderer.render(_loop_model(thumb, ("seed", 6)))

    assert [rect for rect, _p in midway.targets.click] == [rect for rect, _p in later.targets.click]
    assert midway.targets.loop == later.targets.loop
    assert midway.targets.expand == later.targets.expand
    # The row itself never moves at all, head of the loop included; the action
    # button is the one thing that follows the column out along it.
    assert at_start.targets.click[0][0] == midway.targets.click[0][0]
    assert at_start.targets.expand == midway.targets.expand
    seed_buttons = lambda t: [rect for rect, kind in t.loop if kind == "seed"]
    assert seed_buttons(at_start.targets) == seed_buttons(midway.targets)


def test_the_action_column_stands_under_the_lit_cell_mid_loop(thumb):
    """Mid-loop the drawn column is the playing seed's own acts, so it hangs under
    the lit cell — left under the corner it would read as the corner seed's."""
    rendered = HudRenderer("portrait").render(_model(
        locked=False, lock_label="Looping 13 seeds",
        corner=HudCell(path="c.mp4", thumb=thumb),
        seeds=tuple(HudCell(path=f"s{i}.mp4", thumb=thumb) for i in range(12)),
        actions=(HudCell(path="a0.mp4", thumb=thumb, label="Zeta"),),
        active_loop="seed", playing=("seed", 5),
    ))

    rects = {path: rect for rect, path in rendered.targets.click}
    corner_rect = rendered.targets.click[0][0]
    assert rects["a0.mp4"][0] == rects["s5.mp4"][0]  # under the lit cell…
    assert rects["a0.mp4"][0] != corner_rect[0]      # …not under the corner slot


def test_the_map_prints_how_big_each_axis_is(thumb):
    """The map draws only the cells that fit, so its top-left corner carries the
    counts — the only place the real size of each axis can be read.  They are always
    there, loop or no loop."""
    renderer = HudRenderer("portrait")

    def corner_ink(**counts) -> int:
        rendered = renderer.render(_model(corner=HudCell(path="c.mp4", thumb=thumb), **counts))
        (cx, cy, _cw, _ch), _path = rendered.targets.click[0]
        # The block left of the map and above its first row, below the status and
        # control bands: the "Seed N" column headers live to the right of it, over
        # the thumbnails.
        block = _rgb(rendered.bgra)[PAD + STATUS_BAND_H + CTRL_BAND_H:cy, PAD:cx - MAP_GAP]
        return int((block > 80).sum())

    assert corner_ink(seed_count=12, action_count=4) > 0
    assert corner_ink() == 0  # nothing to say before the index has answered


def test_the_filtered_actions_label_is_lit(thumb):
    """A filter shows on the map, on the row it holds you to — so which act you are
    filtered to is readable where you would act on it, beside the lit button that
    lifts it."""
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
        return int((band > 200).sum())  # near-white only; a plain label is gray

    assert gutter_ink("alpha") > 0
    assert gutter_ink("") == 0
    assert gutter_ink("gamma") == 0  # …that row's label lights, not this one


def _filter_button_fill(rendered, action: str) -> int:
    """How much white the *action* row's filter button carries — its lit state.

    White, not green: green across these HUDs means the favorites and the
    funscripts, and a filter is neither.  Only the lock keeps the color.
    """
    x, y, w, h = dict((name, rect) for rect, name in rendered.targets.filter)[action]
    rgb = _rgb(rendered.bgra)[y:y + h, x:x + w].astype(int)
    return int((rgb > 240).all(axis=2).sum())


def test_the_filter_button_lights_on_the_act_the_side_is_filtered_to(thumb):
    """The filter button is the loop buttons' twin — lit while its row is one the
    filter keeps, so the control that lifts a filter is the lit one that set it, and
    a filter set any other way still shows on the row it holds you to."""
    renderer = HudRenderer("portrait")

    def lit_ink(filter_query: str) -> int:
        return _filter_button_fill(renderer.render(_model(
            corner=HudCell(path="c.mp4", thumb=thumb),
            actions=(HudCell(path="a1.mp4", thumb=thumb, label="gamma"),),
            current_action="alpha", filter_query=filter_query,
        )), "alpha")

    assert lit_ink("alpha") > 0
    assert lit_ink("") == 0
    assert lit_ink("gamma") == 0  # …that row's button lights, not this one


def test_a_row_the_filter_only_partly_matches_still_lights(thumb):
    """fun_time keeps a "POV Gamma" clip under a "gamma" filter, so its row stays
    lit while it is on screen.  Lighting only an exact "gamma" put the mark out the
    moment such a clip came up and back on at the next exact one — the panel saying
    the filter had dropped while the playlist under it had not changed at all."""
    renderer = HudRenderer("portrait")

    def lit_ink(current_action: str) -> int:
        return _filter_button_fill(renderer.render(_model(
            corner=HudCell(path="c.mp4", thumb=thumb),
            current_action=current_action, filter_query="gamma",
        )), current_action)

    assert lit_ink("gamma") > 0
    assert lit_ink("pov gamma") > 0       # the query is one word of the act
    assert lit_ink("gamma, theta") > 0    # one of the clip's two acts
    assert lit_ink("alpha") == 0


def _white_halves(rendered) -> tuple[int, int]:
    """Near-white ink across the corner row's act names, split into the row's upper
    and lower halves — a row carrying two acts draws one in each.

    Measured past the filter button at the head of the row: that button fills white
    when it is lit, which is the same ink the labels use.
    """
    (cx, cy, _cw, ch), _path = rendered.targets.click[0]
    band = (_rgb(rendered.bgra)[cy:cy + ch, PAD + FILTER_ROOM:cx - MAP_GAP] > 200).all(axis=2)
    return int(band[:ch // 2].sum()), int(band[ch // 2:].sum())


def test_only_the_act_the_filter_matched_is_lit_on_a_two_act_row(thumb):
    """A clip can carry two acts ("Gamma, Theta") and a one-act filter keeps it for
    one of them, so only that one goes white — lighting the whole label named an act
    the filter has nothing to do with.  The acts stack in order, so the lit one moves
    between the halves of the row as the query changes."""
    renderer = HudRenderer("portrait")

    def halves(filter_query: str) -> tuple[int, int]:
        return _white_halves(renderer.render(_model(
            corner=HudCell(path="c.mp4", thumb=thumb),
            current_action="gamma, theta", filter_query=filter_query,
        )))

    gamma_top, gamma_bottom = halves("gamma")
    theta_top, theta_bottom = halves("theta")

    assert gamma_top > 0 and gamma_bottom == 0
    assert theta_bottom > 0 and theta_top == 0
    assert halves("alpha") == (0, 0)


def test_a_filter_set_from_a_two_act_clip_lights_both_of_its_acts(thumb):
    """Pressing a two-act row's button filters to both acts, and fun_time keeps the
    clip for both — so both go white.  Neither did: the two-act query matched neither
    act on its own, so the row you had just filtered to read as unfiltered."""
    rendered = HudRenderer("portrait").render(_model(
        corner=HudCell(path="c.mp4", thumb=thumb),
        current_action="gamma, theta motion", filter_query="gamma, theta motion",
    ))

    top, bottom = _white_halves(rendered)

    assert top > 0 and bottom > 0


@pytest.mark.parametrize("camera", ["pov", "side"])
def test_a_leading_camera_word_stays_gray_when_its_act_is_filtered(camera, thumb):
    """A camera word in front of an act is drawn as an act of its own: under a
    "gamma" filter only "Gamma" is why the clip is here, so the camera word stays
    gray rather than reading as part of what was asked for.

    Both words, because Evolver's backfill scopes every act it records by one of
    them — so a list holding only "POV" would leave every "Side …" clip lighting
    both of its words.
    """
    renderer = HudRenderer("portrait")

    def halves(filter_query: str) -> tuple[int, int]:
        return _white_halves(renderer.render(_model(
            corner=HudCell(path="c.mp4", thumb=thumb),
            current_action=f"{camera} gamma", filter_query=filter_query,
        )))

    scope, gamma = halves("gamma")

    assert scope == 0 and gamma > 0
    # …and a filter on the row itself names both, so both light.
    assert all(half > 0 for half in halves(f"{camera} gamma"))


def test_the_filter_button_carries_a_funnel_and_not_an_empty_box(thumb):
    """The funnel is drawn rather than typed — no face on the machine carries one —
    so what has to hold is that there is a mark inside the button's border at all:
    an empty box says nothing about what the button does."""
    rendered = HudRenderer("portrait").render(
        _model(corner=HudCell(path="c.mp4", thumb=thumb), current_action="alpha"))

    x, y, w, h = dict((name, rect) for rect, name in rendered.targets.filter)["alpha"]
    # Inside the rounded border, so the box itself can't be what is counted.
    inside = _rgb(rendered.bgra)[y + 3:y + h - 3, x + 3:x + w - 3]
    assert (inside > 80).all(axis=2).sum() > 0


def test_a_long_action_name_is_never_drawn_over_its_filter_button(thumb):
    """The gutter holds the button and the name beside it, so the name is sized into
    what is left rather than reaching back across the button — which would stamp a
    funnel through the middle of the word."""
    renderer = HudRenderer("portrait")

    def button_pixels(action: str) -> np.ndarray:
        rendered = renderer.render(
            _model(corner=HudCell(path="c.mp4", thumb=thumb), current_action=action))
        x, y, w, h = dict((name, rect) for rect, name in rendered.targets.filter)[action]
        return _rgb(rendered.bgra)[y:y + h, x:x + w]

    assert np.array_equal(button_pixels("motion"), button_pixels("iota"))


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


def test_a_tooltip_longer_than_the_panel_is_wide_stays_on_the_panel(thumb):
    """The reported bug: the trash button's tooltip wants more width than a
    portrait panel has, so it was drawn straight off the right edge and read
    "…when it is not a favo".  It wraps now, which is player_core's job — this
    guards that the satellite actually hands it the panel's own bounds, since
    passing anything wider would put the box back over the edge."""
    renderer = HudRenderer("portrait")
    model = _model(corner=HudCell(path="c.mp4", thumb=thumb))
    plain = _rgb(renderer.render(model).bgra)
    tipped = _rgb(renderer.render(model, hover_tip=CONTROL_TOOLTIPS["trash"],
                                 hover_pos=(33, 44)).bgra)

    def edge_ink(rgb) -> int:
        return int((rgb[:, -2:] > 200).all(axis=2).sum())

    assert not np.array_equal(plain, tipped)  # it drew something
    assert edge_ink(tipped) == edge_ink(plain) == 0


def test_the_button_glyphs_are_not_tofu():
    """Segoe UI has no U+21BB, so drawing the loop button with the UI face gives a
    ".notdef" box.  Qt fell back to Segoe UI Symbol silently; Pillow does not, so
    the glyph font must cover every button icon itself — the map's two and each of
    the side's own controls, reset's backwards loop included."""
    from player_core.hud_panel import load_font

    from satellite.hud_paint import (
        _CONTROL_GLYPHS,
        _EXPAND_GLYPH,
        _LOOP_GLYPH,
        _SYMBOL_FONT,
    )

    glyph_font = load_font(11, _SYMBOL_FONT)
    notdef = glyph_font.getmask("").getbbox()

    assert glyph_font.getmask(_LOOP_GLYPH).getbbox() != notdef
    assert glyph_font.getmask(_EXPAND_GLYPH).getbbox() != notdef
    for name, glyph in _CONTROL_GLYPHS.items():
        assert glyph_font.getmask(glyph).getbbox() != notdef, name


def test_the_reset_button_is_never_lit():
    """The lock and F-mode are states the side sits in, so they light while they
    are on; a reset is over the moment it lands, and a button that stayed lit
    would say the side was sitting in one."""
    for locked, f_mode in ((False, False), (True, True)):
        rendered = HudRenderer("landscape").render(
            HudModel(side="landscape", lock_label="Locked",
                     locked=locked, f_mode=f_mode))
        rects = {name: rect for rect, name in rendered.targets.control}
        x, y, w, h = rects["reset"]
        box = _rgb(rendered.bgra)[y:y + h, x:x + w]
        # A lit button fills its box, so most of it would be the on-color.
        filled = (box > 100).all(axis=2).sum()

        assert filled < w * h // 2, (locked, f_mode)


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
    # The header strip sits above the thumbnails — under the status and control
    # bands, which is what the two band heights step past.  Nothing may be drawn in
    # the gap between the corner column and the next one.
    strip_y = PAD + STATUS_BAND_H + CTRL_BAND_H
    header = _rgb(rendered.bgra)[strip_y:strip_y + COL_LABEL_H, cx + cw:sx]
    assert (header > 60).sum() == 0
