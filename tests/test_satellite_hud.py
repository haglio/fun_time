"""The satellite's in-video lock HUD: model, geometry and hit-testing."""
from __future__ import annotations

import json

from satellite.hud import (
    LOCK_BAND_H,
    LOOP_BTN,
    MAP_GAP,
    ROW_GAP,
    HudCell,
    HudClicks,
    HudTargets,
    action_label_blocks,
    build_click_targets,
    build_label_targets,
    button_tooltip,
    expand_button_rect,
    friendly_action_label,
    hit_test_targets,
    loop_button_rects,
    map_window,
    parse_hud,
    status_band_height,
    thumbnail_rects,
    wrap_status_line,
)


# --- the status line ---------------------------------------------------------

# One "character" per 10px, so the arithmetic in these tests is readable.
def _measure(text: str) -> int:
    return 10 * len(text)


def test_a_status_line_that_fits_stays_on_one_line():
    assert wrap_status_line("Locked · Shuffle", 200, _measure) == ["Locked · Shuffle"]


def test_a_status_line_too_wide_for_the_panel_wraps_at_its_separators():
    """The portrait panel is as narrow as its clips, and the line grew a fourth
    part with F-mode.  Pillow clips silently at the panel edge, so an unwrapped
    line does not look long — it looks like the state it ran out of room for is
    switched off."""
    label = "Looping actions · Latest · F-Mode · beta gamma"

    assert wrap_status_line(label, 280, _measure) == [
        "Looping actions · Latest",
        "F-Mode · beta gamma",
    ]


def test_a_single_part_wider_than_the_panel_still_gets_its_own_line():
    """Breaking mid-phrase would read as two states rather than one, so an
    over-wide part is left whole and simply overhangs."""
    assert wrap_status_line("Unlocked · a very long filter phrase indeed", 150, _measure) == [
        "Unlocked",
        "a very long filter phrase indeed",
    ]


def test_an_empty_status_line_needs_no_lines_at_all():
    assert wrap_status_line("", 280, _measure) == []


def test_the_status_band_grows_by_a_line_for_each_wrap():
    """Everything below is laid out from the band's foot, so a second line has to
    push the map down rather than being drawn over its first row."""
    assert status_band_height(1) == LOCK_BAND_H
    assert status_band_height(2) > status_band_height(1)
    assert status_band_height(3) - status_band_height(2) == (
        status_band_height(2) - status_band_height(1)
    )


def test_a_panel_with_nothing_to_say_keeps_the_band_it_always_had():
    """The map's own geometry is measured off the band, so an empty status must not
    move it — the map sits where it sits whatever the line says."""
    assert status_band_height(0) == LOCK_BAND_H


def test_parse_hud_reads_whether_this_side_has_the_floor():
    """Absent means idle: a satellite reading a panel written before the flag
    existed must not light its dot on a missing key."""
    assert parse_hud(json.dumps({"side": "portrait", "active": True})).active is True
    assert parse_hud(json.dumps({"side": "portrait", "active": False})).active is False
    assert parse_hud(json.dumps({"side": "portrait"})).active is False


def test_parse_hud_reads_the_panel_fun_time_published():
    text = json.dumps({
        "side": "portrait",
        "locked": True,
        "lock_label": "Locked · Shuffle · alpha",
        "active_loop": "seed",
        "playing": ["seed", 1],
        "current_action": "alpha",
        "corner": {"path": "C:/v/cur.mp4", "thumb": "C:/t/cur.jpg"},
        "seeds": [{"path": "C:/v/s1.mp4", "thumb": "C:/t/s1.jpg"}],
        "actions": [{"path": "C:/v/a1.mp4", "thumb": "C:/t/a1.jpg", "label": "gamma"}],
    })

    model = parse_hud(text)

    assert model is not None
    assert model.side == "portrait"
    assert model.locked is True
    assert model.lock_label == "Locked · Shuffle · alpha"
    assert model.active_loop == "seed"
    assert model.playing == ("seed", 1)
    assert model.current_action == "alpha"
    assert model.corner == HudCell(path="C:/v/cur.mp4", thumb="C:/t/cur.jpg")
    assert model.seeds == (HudCell(path="C:/v/s1.mp4", thumb="C:/t/s1.jpg"),)
    assert model.actions == (HudCell(path="C:/v/a1.mp4", thumb="C:/t/a1.jpg", label="gamma"),)


def test_parse_hud_defaults_an_empty_panel():
    """A satellite with nothing to map (no clip yet) still parses — it simply has
    no corner, so nothing is drawn."""
    model = parse_hud(json.dumps({"side": "landscape", "locked": False, "lock_label": "Unlocked"}))

    assert model is not None
    assert model.corner is None
    assert model.seeds == ()
    assert model.actions == ()
    assert model.playing == ("corner", 0)


def test_parse_hud_rejects_junk():
    """A half-written file (fun_time writes it while the player reads) must not
    crash the player — it just keeps the HUD it already had."""
    assert parse_hud('{"side": "portrait"') is None
    assert parse_hud("") is None


# --- the window a long loop is drawn through ---------------------------------
# 30px cells with MAP_GAP (5) between them: 110px of room holds exactly three
# (30 + 35 + 35), which is about what a real satellite panel fits.
_CELLS = [30] * 12
_ROOM_FOR_THREE = 110


def test_a_loop_short_enough_to_fit_is_drawn_whole():
    window = map_window([30, 30, 30], playing=0, available=_ROOM_FOR_THREE)

    assert (window.start, window.count) == (0, 3)
    assert window.more_before is False
    assert window.more_after is False


def test_a_loop_just_started_opens_on_the_clip_on_screen():
    """The loop's head is the clip it started on, so at that moment the window opens
    there — the clip you pressed loop on is drawn in the corner, never mid-row."""
    window = map_window(_CELLS, playing=0, available=_ROOM_FOR_THREE)

    assert (window.start, window.count) == (0, 3)
    assert window.more_after is True
    assert window.more_before is False


def test_a_loop_partway_through_keeps_the_clip_on_screen_in_the_middle():
    """Once the loop has advanced past the first cells the window slides with it, so
    the lit thumbnail stays in the middle instead of walking off the end of the map
    and leaving nothing highlighted."""
    window = map_window(_CELLS, playing=5, available=_ROOM_FOR_THREE)

    assert (window.start, window.count) == (4, 3)  # 4, 5, 6 — the playing one centred
    assert window.more_before is True
    assert window.more_after is True


def test_a_loop_near_its_end_clamps_rather_than_running_off():
    window = map_window(_CELLS, playing=11, available=_ROOM_FOR_THREE)

    assert (window.start, window.count) == (9, 3)
    assert window.more_before is True
    assert window.more_after is False


def test_a_cell_too_big_for_the_room_is_still_drawn():
    """A clipped thumbnail beats a map with nothing on it at all."""
    window = map_window([300], playing=0, available=100)

    assert (window.start, window.count) == (0, 1)


def test_an_empty_axis_has_no_window():
    window = map_window([], playing=0, available=200)

    assert (window.start, window.count) == (0, 0)


def _label_targets(*labels: str) -> HudTargets:
    """Click targets for a gutter of action labels, stacked 20px apart."""
    return HudTargets(
        click=[], loop=[],
        label=[((0, i * 20, 40, 20), label) for i, label in enumerate(labels)],
        expand=None,
    )


def test_clicking_an_action_label_filters_the_side_to_it():
    clicks = HudClicks("portrait")

    assert clicks.press(_label_targets("Theta Motion"), 5, 5, now=0.0) == "filter_portrait_theta_motion"


def test_clicking_the_lit_action_label_turns_the_filter_off():
    """The label is a toggle: it filters to that act, and pressing the lit one drops
    the filter — so the way out is the same control as the way in."""
    clicks = HudClicks("portrait")
    clicks.active_filter = "alpha"

    assert clicks.press(_label_targets("Alpha"), 5, 5, now=0.0) == "portrait_no_filter"


def test_clicking_another_label_while_filtered_moves_the_filter():
    clicks = HudClicks("portrait")
    clicks.active_filter = "alpha"

    assert clicks.press(_label_targets("Gamma"), 5, 5, now=0.0) == "filter_portrait_gamma"


def test_thumbnail_rects_positions_the_map_and_drops_overflow():
    """The corner anchors the map; seeds walk right and actions walk down, each
    dropped (not clipped) when it would cross the panel edge."""
    corner, seeds, actions = thumbnail_rects(
        map_x=100, map_y=50, right=300, bottom=280,
        corner_size=(30, 54),
        seed_sizes=[(30, 54), (30, 54), (200, 54)],   # the third would cross right=300
        action_sizes=[(30, 54), (30, 200)],           # the second would cross bottom=280
    )

    assert corner == (100, 50, 30, 54)
    s1 = 100 + 30 + MAP_GAP
    s2 = s1 + 30 + MAP_GAP
    assert seeds == [(s1, 50, 30, 54), (s2, 50, 30, 54)]   # third dropped
    assert actions == [(100, 50 + 54 + ROW_GAP, 30, 54)]   # second dropped


def test_loop_button_rects_places_below_the_column_and_right_of_the_row():
    corner = (10, 10, 20, 20)
    loop_action, loop_seed = loop_button_rects(
        corner, [(35, 10, 20, 20)], [(10, 35, 20, 20)], right=200, bottom=200,
    )

    assert loop_action == (10, 35 + 20 + MAP_GAP, 20, LOOP_BTN)   # below the lowest action
    assert loop_seed == (35 + 20 + MAP_GAP, 10, LOOP_BTN, 20)     # right of the rightmost seed

    # A panel too small for either drops it rather than overflowing.
    assert loop_button_rects(
        corner, [(35, 10, 20, 20)], [(10, 35, 20, 20)], right=70, bottom=70,
    ) == (None, None)
    assert loop_button_rects(None, [], [], right=200, bottom=200) == (None, None)


def test_expand_button_sits_in_the_row_right_of_the_seed_loop_button():
    """The expand ("more seeds") button lives in the seed row, just right of the
    seed-loop button, and hides rather than overflow the panel's right edge."""
    loop_seed = (60, 10, 18, 30)

    assert expand_button_rect(loop_seed, right=200) == (60 + 18 + MAP_GAP, 10, LOOP_BTN, 30)
    assert expand_button_rect(None, right=200) is None
    assert expand_button_rect(loop_seed, right=90) is None  # no room -> dropped


def test_build_and_hit_test_click_targets():
    """Targets zip the drawn rects to their paths — corner=current, then each
    seed, then each action — and a point resolves to the clip it falls in."""
    corner = (10, 10, 20, 20)
    seeds = [(40, 10, 20, 20)]
    actions = [(10, 40, 20, 20)]

    targets = build_click_targets(
        corner, seeds, actions,
        HudCell(path="cur.mp4"), [HudCell(path="s1.mp4")], [HudCell(path="a1.mp4")],
    )

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
    assert build_click_targets(None, [], [], None, [], []) == []


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


def test_button_tooltip_names_each_button():
    loop_targets = [((0, 0, 20, 20), "action"), ((30, 0, 20, 20), "seed")]
    expand = (30, 30, 18, 18)

    assert button_tooltip(loop_targets, expand, 5, 5) == "Loop this action column"
    assert button_tooltip(loop_targets, expand, 35, 5) == "Loop this seed row"
    assert button_tooltip(loop_targets, expand, 35, 35) == "More seeds — widen the net"
    assert button_tooltip(loop_targets, expand, 200, 200) == ""


def test_action_label_blocks_separate_comma_joined_acts():
    """Several acts on one clip ("Alpha, Theta Motion") become one block each
    (drawn with a gap between), commas dropped; one act is a single block."""
    assert action_label_blocks("alpha, theta motion") == [["Alpha"], ["Theta", "Motion"]]
    assert action_label_blocks("pov gamma") == [["POV", "Gamma"]]
    assert action_label_blocks("") == [["(unknown)"]]


def test_friendly_action_label_titlecases_and_keeps_acronyms_upper():
    assert friendly_action_label("epsilon") == "Epsilon"
    assert friendly_action_label("pov gamma") == "POV\nGamma"
    # A long single word stays whole (the gutter is sized to fit it).
    assert friendly_action_label("delta") == "Delta"
    assert friendly_action_label("   ") == "(unknown)"


def _targets(**overrides) -> HudTargets:
    base = dict(click=[], loop=[], label=[], expand=None)
    base.update(overrides)
    return HudTargets(**base)


def test_single_click_switches_and_double_click_locks():
    """A single click posts play_video once its double-click window lapses; a
    second click inside that window cancels it and posts lock_video instead."""
    clicks = HudClicks("landscape")
    targets = _targets(click=[((0, 0, 30, 30), "C:/v/pick.mp4")])

    assert clicks.press(targets, 10, 10, now=0.0) == ""      # deferred
    assert clicks.due(now=0.1) == ""                          # still inside the window
    assert clicks.due(now=1.0) == "landscape_play_video|C:/v/pick.mp4"
    assert clicks.due(now=2.0) == ""                          # fired once

    assert clicks.press(targets, 10, 10, now=10.0) == ""
    assert clicks.press(targets, 10, 10, now=10.2) == "landscape_lock_video|C:/v/pick.mp4"
    assert clicks.due(now=11.0) == ""                         # the single was cancelled


def test_clicking_empty_space_posts_nothing():
    clicks = HudClicks("portrait")
    assert clicks.press(_targets(), 200, 200, now=0.0) == ""
    assert clicks.due(now=5.0) == ""


def test_loop_buttons_toggle_and_are_mutually_exclusive():
    """Clicking a loop button posts action_loop/seed_loop and marks it active; the
    other going on turns it off (they cannot coexist); clicking the active one
    again posts no_loop."""
    clicks = HudClicks("portrait")
    targets = _targets(loop=[((0, 0, 20, 20), "action"), ((30, 0, 20, 20), "seed")])

    assert clicks.press(targets, 5, 5, now=0.0) == "portrait_action_loop"
    assert clicks.active_loop == "action"
    assert clicks.press(targets, 35, 5, now=1.0) == "portrait_seed_loop"
    assert clicks.active_loop == "seed"
    assert clicks.press(targets, 35, 5, now=2.0) == "portrait_no_loop"
    assert clicks.active_loop == ""


def test_clicking_the_expand_button_posts_more_seeds():
    clicks = HudClicks("landscape")
    assert clicks.press(_targets(expand=(0, 0, 18, 18)), 5, 5, now=0.0) == "landscape_more_seeds"


def test_clicking_an_action_label_filters_to_that_action():
    """A click on a row's action name posts filter_<side>_<action>, the same
    command speaking "[side] gamma" would."""
    clicks = HudClicks("portrait")
    targets = _targets(label=[((0, 0, 50, 20), "Gamma")])

    assert clicks.press(targets, 5, 5, now=0.0) == "filter_portrait_gamma"


def test_clicking_a_two_word_action_label_slugs_it():
    """Multi-word acts carry an underscore in the command, as filter_vocab slugs
    them ("beta gamma" -> beta_gamma)."""
    clicks = HudClicks("landscape")
    targets = _targets(label=[((0, 0, 50, 20), "Beta Gamma")])

    assert clicks.press(targets, 5, 5, now=0.0) == "filter_landscape_beta_gamma"
