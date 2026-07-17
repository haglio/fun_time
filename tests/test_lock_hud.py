from __future__ import annotations

import json
import textwrap
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.lock_hud import (
    HudAppConfig,
    build_hud_panel,
    build_panels,
    cell_path,
    hud_map_cells,
    hud_overlays_visible,
    hud_should_be_topmost,
    load_fun_time_pids,
    load_hud_app_config,
    locate_cell,
    navigate_cell,
    overlay_rect,
    panel_thumbnails,
    prewarm_thumbnails,
    prime_group_indexes,
    signal_hud_ready,
    wait_for_hud_ready,
)
from fun_time.media_metadata import (
    GroupIndex,
    metadata_path_for,
    normalize_path_key as K,
    reset_group_index_cache,
)
from fun_time.window_layout import WindowRect

CUR = "C:/vids/current.mp4"
A1 = "C:/vids/action1.mp4"
A2 = "C:/vids/action2.mp4"
S1 = "C:/vids/seed1.mp4"


def _index(*, current: str, action_sibs=(), seed_sibs=()) -> GroupIndex:
    action_all = sorted([current, *action_sibs])
    seed_all = sorted([current, *seed_sibs])
    # An action group varies the act; a seed family repeats the current clip's
    # act under other seeds — seed_family_members narrows on exactly that.
    action_by_path = {K(current): "Alpha"}
    action_by_path.update({K(p): f"Act{i}" for i, p in enumerate(action_sibs)})
    action_by_path.update({K(p): "Alpha" for p in seed_sibs})
    return GroupIndex(
        action_key_by_path={K(p): "A" for p in action_all},
        action_members={"A": action_all},
        action_by_path=action_by_path,
        seed_key_by_path={K(p): ("S", str(i)) for i, p in enumerate(seed_all)},
        seed_members={"S": seed_all},
        loose_seed_key_by_path={},
        loose_seed_members={},
        indexed_paths=frozenset(K(p) for p in (current, *action_sibs, *seed_sibs)),
    )


def test_hud_overlays_visible_only_hides_during_loading():
    """Shown whenever the loading overlay is down — OmniPause included, so the
    map stays up while paused."""
    assert hud_overlays_visible(loading_active=True) is False
    assert hud_overlays_visible(loading_active=False) is True


def test_hud_should_be_topmost_only_when_a_fun_time_window_is_foreground():
    """The overlay holds the topmost band only while the focused window belongs
    to Fun Time; a stranger in front (the user switched away, e.g. during
    OmniPause) means drop, so the HUD never floats over their other apps."""
    ours = {1000, 2000, 3000}
    assert hud_should_be_topmost(2000, ours) is True    # a Fun Time window is focused
    assert hud_should_be_topmost(9999, ours) is False   # another app is focused
    assert hud_should_be_topmost(0, ours) is False      # no foreground window at all


def test_load_fun_time_pids_reads_recorded_children_and_unions_own_pid(tmp_path):
    """The session's window-owning PIDs (satellites, players, dashboard) come from
    the orchestrator's bridge_pids.ini; our own PID is added too.  A never-launched
    child (pid 0) can own no window, so it is dropped."""
    pids_file = tmp_path / "bridge_pids.ini"
    pids_file.write_text(textwrap.dedent("""\
        [pids]
        nau_pid = 200
        portrait_pid = 300
        landscape_pid = 400
        genau_pid = 0
        [created_at]
        nau_pid = 2000
    """), encoding="utf-8")

    assert load_fun_time_pids(pids_file, own_pid=42) == {42, 200, 300, 400}


def test_load_fun_time_pids_without_a_file_is_just_our_own_pid(tmp_path):
    """Before startup writes bridge_pids.ini (or if it is unreadable) the only PID
    we can vouch for is our own — so every other window reads as foreign and the
    overlay drops, rather than staying stuck on top."""
    assert load_fun_time_pids(tmp_path / "missing.ini", own_pid=42) == {42}


def test_panel_gathers_action_and_seed_siblings_and_labels_the_lock():
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    panel = build_hud_panel("portrait", locked=True, current=CUR, index=index)

    assert panel.side == "portrait"
    assert panel.locked is True
    assert panel.lock_label == "Locked"
    assert panel.current == CUR
    assert panel.action_siblings == sorted([A1, A2])
    assert panel.seed_siblings == [S1]


def test_panel_carries_axis_labels_for_the_map():
    """The map's rows are named by action: the current clip's own action labels
    the top row, and each action sibling carries its action name, in step with
    action_siblings, so the HUD can draw the row labels."""
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    panel = build_hud_panel("portrait", locked=True, current=CUR, index=index)

    assert panel.current_action == "Alpha"
    # action_siblings is sorted([A1, A2]); A1→"Act0", A2→"Act1" in _index.
    assert panel.action_labels == ("Act0", "Act1")
    assert len(panel.action_labels) == len(panel.action_siblings)


def test_panel_labels_an_unlocked_satellite():
    index = _index(current=CUR, action_sibs=[A1])

    panel = build_hud_panel("landscape", locked=False, current=CUR, index=index)

    assert panel.locked is False
    assert panel.lock_label == "Unlocked"
    assert panel.action_siblings == [A1]  # siblings show whether locked or not


def test_panel_folds_a_future_lock_type_into_the_label():
    index = _index(current=CUR)

    panel = build_hud_panel("portrait", locked=True, current=CUR, index=index, lock_type="seed")

    assert panel.lock_label == "Locked · seed"


def test_panel_without_a_current_video_has_no_siblings():
    index = _index(current=CUR, action_sibs=[A1], seed_sibs=[S1])

    panel = build_hud_panel("portrait", locked=False, current="", index=index)

    assert panel.action_siblings == []
    assert panel.seed_siblings == []


def test_panel_carries_the_active_filter():
    index = _index(current=CUR)

    panel = build_hud_panel(
        "portrait", locked=False, current=CUR, index=index, filter_query="beta gamma"
    )

    assert panel.filter_query == "beta gamma"
    assert build_hud_panel("portrait", locked=False, current=CUR, index=index).filter_query == ""


def test_without_a_loop_the_map_anchors_on_the_live_clip():
    index = _index(current=CUR, seed_sibs=[S1])

    panel = build_hud_panel("portrait", locked=False, current=CUR, index=index)

    assert panel.active_loop == ""
    assert panel.current == CUR
    assert panel.playing == CUR  # the corner is what's on


def test_a_seed_loop_freezes_the_map_on_the_family_anchor():
    """While the seed row loops, the map anchors on the family's fixed member
    (its lowest-keyed clip) so it holds still, and ``playing`` follows the clip
    actually on screen so the overlay can light it up."""
    index = _index(current=CUR, seed_sibs=[S1])

    # S1 is on screen, but CUR sorts first, so the map anchors on CUR.
    panel = build_hud_panel("portrait", locked=False, current=S1, index=index, loop_axis="seed")

    assert panel.active_loop == "seed"
    assert panel.current == CUR       # frozen anchor, not the live clip
    assert panel.playing == S1        # the seed actually playing
    assert panel.seed_siblings == [S1]


def test_an_action_loop_freezes_the_map_and_marks_the_playing_action():
    index = _index(current=CUR, action_sibs=[A1])

    # CUR is on screen; A1 sorts first, so the column anchors on A1 and the
    # playing cell is the sibling that carries CUR's action.
    panel = build_hud_panel("portrait", locked=False, current=CUR, index=index, loop_axis="action")

    assert panel.active_loop == "action"
    assert panel.current == A1
    assert panel.playing == CUR
    assert panel.action_siblings == [CUR]


def test_widen_grows_the_seed_row_to_the_loose_family():
    """"more seeds" widens the display: the seed row grows from the exact family to
    the loose family — the same scene re-rendered with a render knob freed — without
    the current clip changing."""
    other = "C:/vids/other.mp4"
    index = GroupIndex(
        action_key_by_path={K(CUR): "g1", K(S1): "g1", K(other): "g2"},
        action_members={"g1": sorted([CUR, S1]), "g2": [other]},
        action_by_path={K(CUR): "Alpha", K(S1): "Alpha", K(other): "Alpha"},
        seed_key_by_path={K(CUR): ("S", "0"), K(S1): ("S", "1")},
        seed_members={"S": sorted([CUR, S1])},
        # The loose family is the strict one plus `other` — same scene, a knob freed.
        loose_seed_key_by_path={K(CUR): ("L", "0"), K(S1): ("L", "1"), K(other): ("L", "2")},
        loose_seed_members={"L": sorted([CUR, S1, other])},
        indexed_paths=frozenset({K(CUR), K(S1), K(other)}),
    )

    narrow = build_hud_panel("portrait", locked=False, current=CUR, index=index)
    wide = build_hud_panel("portrait", locked=False, current=CUR, index=index, widen_clip=CUR)

    assert narrow.seed_siblings == [S1]                 # exact family only
    assert set(wide.seed_siblings) == {S1, other}       # widened to the loose family
    assert wide.current == CUR                          # the clip on screen is unchanged


def test_widen_off_a_loop_resets_once_its_anchor_clip_leaves_the_screen():
    """Without a loop, the widen holds only while its exact anchor is on screen —
    a plain auto-advance to another clip drops it (the same-clip reset)."""
    other = "C:/vids/other.mp4"
    index = GroupIndex(
        action_key_by_path={K(CUR): "g1", K(S1): "g1", K(other): "g2"},
        action_members={"g1": sorted([CUR, S1]), "g2": [other]},
        action_by_path={K(CUR): "Alpha", K(S1): "Alpha", K(other): "Alpha"},
        seed_key_by_path={K(CUR): ("S", "0"), K(S1): ("S", "1")},
        seed_members={"S": sorted([CUR, S1])},
        loose_seed_key_by_path={}, loose_seed_members={},
        indexed_paths=frozenset({K(CUR), K(S1), K(other)}),
    )

    # Widened around CUR, but the live clip is now `other` and no loop is running.
    panel = build_hud_panel("portrait", locked=False, current=other, index=index, widen_clip=CUR)

    assert panel.seed_siblings == []  # `other` has no same-act sisters of its own


def test_a_widened_seed_loop_stays_wide_and_frozen_across_the_loose_family():
    """The bug: a seed loop over the loose family auto-advances to a re-render (a
    different exact seed family) that is not in the current clip's exact family. The
    row must stay wide and the map stay frozen on the widened anchor — not collapse
    onto that clip's own exact family with no loop shown."""
    # x, x2 share the exact family F1; y and z are their own renders F2, F3; all four
    # are one loose family L (the same scene, render knobs freed).
    x, x2, y, z = "C:/v/x.mp4", "C:/v/x2.mp4", "C:/v/y.mp4", "C:/v/z.mp4"
    index = GroupIndex(
        action_key_by_path={K(p): "scene" for p in (x, x2, y, z)},
        action_members={"scene": sorted([x, x2, y, z])},
        action_by_path={K(p): "Alpha" for p in (x, x2, y, z)},
        seed_key_by_path={K(x): ("F1", "0"), K(x2): ("F1", "1"), K(y): ("F2", "0"), K(z): ("F3", "0")},
        seed_members={"F1": sorted([x, x2]), "F2": [y], "F3": [z]},
        loose_seed_key_by_path={K(x): ("L", "0"), K(x2): ("L", "1"), K(y): ("L", "2"), K(z): ("L", "3")},
        loose_seed_members={"L": sorted([x, x2, y, z])},
        indexed_paths=frozenset(K(p) for p in (x, x2, y, z)),
    )

    # The loop was widened around x; VLC has auto-advanced to y, a loose-family
    # re-render that is NOT in x's exact seed family {x, x2}.
    panel = build_hud_panel(
        "portrait", locked=False, current=y, index=index, loop_axis="seed", widen_clip=x,
    )

    assert panel.active_loop == "seed"                 # still looping — not reset
    assert panel.current == x                          # frozen on the widened anchor (min key)
    assert panel.playing == y                          # the widened member actually on screen
    assert set(panel.seed_siblings) == {x2, y, z}      # the whole loose family, minus the anchor


def test_a_non_widened_seed_loop_ignores_a_cleared_widen_anchor():
    """With no widen anchor, a seed loop stays on the exact family even when the
    live clip has loose-family kin — the widen is opt-in."""
    x, x2, y = "C:/v/x.mp4", "C:/v/x2.mp4", "C:/v/y.mp4"
    index = GroupIndex(
        action_key_by_path={K(p): "scene" for p in (x, x2, y)},
        action_members={"scene": sorted([x, x2, y])},
        action_by_path={K(p): "Alpha" for p in (x, x2, y)},
        seed_key_by_path={K(x): ("F1", "0"), K(x2): ("F1", "1"), K(y): ("F2", "0")},
        seed_members={"F1": sorted([x, x2]), "F2": [y]},
        loose_seed_key_by_path={K(x): ("L", "0"), K(x2): ("L", "1"), K(y): ("L", "2")},
        loose_seed_members={"L": sorted([x, x2, y])},
        indexed_paths=frozenset(K(p) for p in (x, x2, y)),
    )

    panel = build_hud_panel(
        "portrait", locked=False, current=x2, index=index, loop_axis="seed", widen_clip="",
    )

    assert panel.active_loop == "seed"
    assert panel.current == x                     # anchored on the exact family, not widened
    assert set(panel.seed_siblings) == {x2}       # exact family only — y is not on the row


def test_a_group_of_one_does_not_freeze_the_map():
    """A "loop" over a family of one is really a lock, so there is nothing to
    freeze — the map stays anchored on the live clip and reports no loop."""
    index = _index(current=CUR)

    panel = build_hud_panel("portrait", locked=False, current=CUR, index=index, loop_axis="seed")

    assert panel.active_loop == ""
    assert panel.current == CUR
    assert panel.playing == CUR


def test_nav_anchor_freezes_the_map_on_the_clip_navigation_began_from():
    """Keyboard navigation pins the map to the clip the user began from: while
    the live clip is still one of its cells the corner stays the anchor and
    ``playing`` marks the cell on screen, so a highlight moves across a stable
    map (not a loop — playback still auto-advances)."""
    index = _index(current=CUR, seed_sibs=[S1])

    # Started from CUR; the satellite has since been switched to seed S1.
    panel = build_hud_panel("portrait", locked=False, current=S1, index=index, nav_anchor=CUR)

    assert panel.current == CUR       # frozen on the start clip, not the live one
    assert panel.playing == S1        # the seed actually on screen is lit
    assert panel.seed_siblings == [S1]
    assert panel.active_loop == ""


def test_nav_anchor_lights_a_selected_action_cell():
    index = _index(current=CUR, action_sibs=[A1])

    # Navigated down from CUR onto its action sibling A1.
    panel = build_hud_panel("portrait", locked=False, current=A1, index=index, nav_anchor=CUR)

    assert panel.current == CUR
    assert panel.playing == A1
    assert panel.action_siblings == [A1]


def test_nav_anchor_equal_to_the_live_clip_is_the_ordinary_map():
    """Right after navigation starts (before any switch) the anchor is the live
    clip, so the map is the plain one homed on it."""
    index = _index(current=CUR, seed_sibs=[S1])

    panel = build_hud_panel("portrait", locked=False, current=CUR, index=index, nav_anchor=CUR)

    assert panel.current == CUR
    assert panel.playing == CUR
    assert panel.seed_siblings == [S1]


def test_nav_anchor_re_homes_once_the_clip_drifts_off_the_map():
    """If the satellite auto-advances to a clip that is not on the frozen map, the
    map re-homes on the live clip rather than lying about what is playing."""
    index = _index(current=CUR, seed_sibs=[S1])
    elsewhere = "C:/vids/elsewhere.mp4"  # not on CUR's family — the satellite advanced off it

    panel = build_hud_panel("portrait", locked=False, current=elsewhere, index=index, nav_anchor=CUR)

    assert panel.current == elsewhere   # frozen anchor abandoned
    assert panel.playing == elsewhere
    assert panel.seed_siblings == []


def test_a_loop_takes_precedence_over_a_stale_nav_anchor():
    """A running loop wins the freeze — starting a loop is what clears any nav
    anchor, so if both arrive the loop's frozen group is what shows."""
    index = _index(current=CUR, seed_sibs=[S1])

    panel = build_hud_panel(
        "portrait", locked=False, current=S1, index=index, loop_axis="seed", nav_anchor="C:/vids/other.mp4"
    )

    assert panel.active_loop == "seed"
    assert panel.current == CUR  # the loop's family anchor, not the nav anchor


# --- map navigation geometry ---


def test_navigate_from_the_corner_steps_onto_each_axis():
    """From the anchor, right enters the seed row and down enters the action
    column; left/up stay put — there is nothing above or left of the corner."""
    corner = ("corner", 0)
    assert navigate_cell(corner, "right", seed_count=3, action_count=2) == ("seed", 0)
    assert navigate_cell(corner, "down", seed_count=3, action_count=2) == ("action", 0)
    assert navigate_cell(corner, "left", seed_count=3, action_count=2) == corner
    assert navigate_cell(corner, "up", seed_count=3, action_count=2) == corner


def test_navigate_walks_the_seed_row_and_clamps_at_its_end():
    assert navigate_cell(("seed", 0), "right", seed_count=3, action_count=0) == ("seed", 1)
    assert navigate_cell(("seed", 1), "right", seed_count=3, action_count=0) == ("seed", 2)
    # Already on the last seed — right has nowhere to go.
    assert navigate_cell(("seed", 2), "right", seed_count=3, action_count=0) == ("seed", 2)


def test_navigate_walks_the_seed_row_back_to_the_corner():
    assert navigate_cell(("seed", 1), "left", seed_count=3, action_count=0) == ("seed", 0)
    assert navigate_cell(("seed", 0), "left", seed_count=3, action_count=0) == ("corner", 0)


def test_navigate_walks_the_action_column_and_clamps_at_its_end():
    assert navigate_cell(("action", 0), "down", seed_count=0, action_count=2) == ("action", 1)
    assert navigate_cell(("action", 1), "down", seed_count=0, action_count=2) == ("action", 1)
    assert navigate_cell(("action", 1), "up", seed_count=0, action_count=2) == ("action", 0)
    assert navigate_cell(("action", 0), "up", seed_count=0, action_count=2) == ("corner", 0)


def test_navigate_off_axis_moves_are_no_ops():
    """The map is an L: a seed has nothing below it, an action nothing to its
    right — those moves keep the selection where it is."""
    assert navigate_cell(("seed", 1), "down", seed_count=3, action_count=2) == ("seed", 1)
    assert navigate_cell(("seed", 1), "up", seed_count=3, action_count=2) == ("seed", 1)
    assert navigate_cell(("action", 1), "right", seed_count=3, action_count=2) == ("action", 1)
    assert navigate_cell(("action", 1), "left", seed_count=3, action_count=2) == ("action", 1)


def test_navigate_from_the_corner_onto_an_empty_axis_stays_put():
    corner = ("corner", 0)
    assert navigate_cell(corner, "right", seed_count=0, action_count=2) == corner
    assert navigate_cell(corner, "down", seed_count=3, action_count=0) == corner


def test_locate_cell_matches_the_corner_a_seed_or_an_action():
    seeds = [S1, "C:/vids/seed2.mp4"]
    actions = [A1]
    assert locate_cell(CUR, CUR, seeds, actions) == ("corner", 0)
    assert locate_cell(S1, CUR, seeds, actions) == ("seed", 0)
    assert locate_cell("C:/vids/seed2.mp4", CUR, seeds, actions) == ("seed", 1)
    assert locate_cell(A1, CUR, seeds, actions) == ("action", 0)


def test_locate_cell_is_none_when_the_clip_is_off_the_map():
    """A clip the map does not draw — e.g. after the satellite auto-advanced off
    the family — is reported as absent so the caller can re-home."""
    assert locate_cell("C:/vids/elsewhere.mp4", CUR, [S1], [A1]) is None


def test_locate_cell_matches_case_insensitively():
    """Paths are keyed by ``normalize_path_key`` (a case fold), so a differently
    cased current clip still finds its cell."""
    assert locate_cell("C:/VIDS/SEED1.MP4", CUR, [S1], []) == ("seed", 0)


def test_cell_path_reads_the_clip_at_a_cell():
    seeds = [S1, "C:/vids/seed2.mp4"]
    actions = [A1, A2]
    assert cell_path(("corner", 0), CUR, seeds, actions) == CUR
    assert cell_path(("seed", 1), CUR, seeds, actions) == "C:/vids/seed2.mp4"
    assert cell_path(("action", 1), CUR, seeds, actions) == A2


def test_cell_path_is_empty_for_an_out_of_range_cell():
    assert cell_path(("seed", 5), CUR, [S1], []) == ""
    assert cell_path(("action", 0), CUR, [S1], []) == ""


def test_hud_map_cells_lists_the_drawn_seed_and_action_clips():
    """Navigation walks the same seed row and action column the HUD draws around
    an anchor, so a keyboard selection always lands on a visible thumbnail."""
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    seeds, actions = hud_map_cells(index, CUR)

    assert seeds == [S1]
    assert actions == sorted([A1, A2])


def test_hud_map_cells_caps_each_axis_at_the_draw_limit():
    seed_sibs = [f"C:/vids/seed{i}.mp4" for i in range(9)]
    index = _index(current=CUR, seed_sibs=seed_sibs)

    seeds, _actions = hud_map_cells(index, CUR, seed_limit=6, action_limit=4)

    assert len(seeds) == 6


# --- load_hud_app_config ---


def test_load_hud_app_config_reads_the_bridge_manifest(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text(textwrap.dedent("""
        [layout]
        main_monitor = 1
        secondary_monitor = 2
        primary_top_ratio = 0.7273
        landscape_width_ratio = 0.6667
        [vlc]
        vlc2_port = 8091
        vlc3_port = 8092
        vlc_pass = s3cret
        [media]
        portrait_dirs = C:/vids/portrait|C:/vids/portrait2
        landscape_dirs = C:/vids/landscape
        [provider_regen]
        media_root = C:/vids/AI
        metadata_root = C:/vids/metadata
        [commands]
        dashboard_state_file = C:/state/dashboard_state.ini
    """), encoding="utf-8")

    cfg = load_hud_app_config(manifest)

    assert (cfg.portrait_port, cfg.landscape_port) == (8091, 8092)
    assert cfg.vlc_password == "s3cret"
    assert cfg.portrait_sources == "C:/vids/portrait|C:/vids/portrait2"
    assert cfg.landscape_sources == "C:/vids/landscape"
    assert cfg.provider_media_root == Path("C:/vids/AI")
    assert cfg.provider_metadata_root == Path("C:/vids/metadata")
    assert cfg.shared_state_file == manifest.parent / "shared_bridge_state.ini"
    assert cfg.layout.main_monitor == 1
    assert cfg.layout.secondary_monitor == 2
    assert cfg.thumbnail_cache_dir == manifest.parent / "hud_thumbnails"
    assert cfg.ready_file == manifest.parent / "lock_hud_ready.txt"


def test_load_hud_app_config_tolerates_absent_provider_roots(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text(textwrap.dedent("""
        [layout]
        main_monitor = 1
        secondary_monitor = 2
        primary_top_ratio = 0.7
        landscape_width_ratio = 0.66
        [vlc]
        vlc2_port = 8091
        vlc3_port = 8092
        vlc_pass =
        [media]
        portrait_dirs = C:/vids/portrait
        landscape_dirs = C:/vids/landscape
        [commands]
        dashboard_state_file = C:/state/dashboard_state.ini
    """), encoding="utf-8")

    cfg = load_hud_app_config(manifest)

    assert cfg.provider_media_root is None
    assert cfg.provider_metadata_root is None


# --- overlay_rect ---


def test_overlay_rect_anchors_to_the_top_left_corner_with_a_margin():
    vlc = WindowRect(x=853, y=0, width=1707, height=1392)

    rect = overlay_rect(vlc, width=260, height=180, margin=12)

    assert rect == WindowRect(x=865, y=12, width=260, height=180)


def test_overlay_rect_carries_a_negative_origin_monitor():
    """The portrait monitor can sit at a negative x; the corner must track it."""
    vlc = WindowRect(x=-1440, y=0, width=1440, height=2502)

    rect = overlay_rect(vlc, width=200, height=150, margin=10)

    assert (rect.x, rect.y) == (-1430, 10)


# --- build_panels ---


def _i2v(action: str, video_seed: str, image_seed: str = "100") -> dict:
    return {
        "video": {
            "prompt": "a scene", "model": "Realism", "action": action,
            "resolution": "720x560", "aspect_ratio": "9:7", "quality": "720p",
            "seed": video_seed, "created": "2025-12-05",
        },
        "source_image": {
            "positive_prompt": "two dolls", "negative_prompt": "tan lines",
            "model": "X Sweet", "resolution": "1728x1344", "aspect_ratio": "9:7",
            "quality": "Best", "seed": image_seed, "created": "2025-12-04",
            "style": "Default", "creativity": "7",
        },
    }


def _clip(media_root: Path, metadata_root: Path, name: str, meta: dict) -> str:
    video = media_root / "portrait" / f"{name}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")
    sidecar = metadata_path_for(video, metadata_root)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    return str(video)


def _hud_config(**overrides) -> HudAppConfig:
    base = dict(
        layout=LayoutConfig(1, 2, 0.7, 0.66),
        portrait_port=8091, landscape_port=8092, vlc_password="",
        portrait_sources="", landscape_sources="",
        provider_media_root=None, provider_metadata_root=None,
        shared_state_file=Path("shared_bridge_state.ini"),
        thumbnail_cache_dir=Path("thumbs"),
        dashboard_cmd_file=Path("dashboard_cmd.txt"),
        ready_file=Path("lock_hud_ready.txt"),
    )
    base.update(overrides)
    return HudAppConfig(**base)


def test_prime_group_indexes_builds_both_sides_up_front(tmp_path: Path):
    """Priming builds each side's real index up front and caches it, so a later
    read serves it from memory — no per-clip rebuild during the session."""
    from fun_time.media_metadata import cached_group_index

    reset_group_index_cache()
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    _clip(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    sources = str(media_root / "portrait")
    config = _hud_config(portrait_sources=sources, landscape_sources=sources, provider_metadata_root=metadata_root)

    prime_group_indexes(config)

    # Served from the primed cache: a lazy build here (empty supplier) would be
    # empty, so a non-empty index proves prime populated it from the real tree.
    index = cached_group_index(sources, paths_supplier=lambda: [], metadata_root=metadata_root, must_contain=None)
    assert index.indexed_paths


def test_prewarm_thumbnails_covers_every_clip_in_both_libraries(tmp_path: Path):
    """Every library clip is thumbnailed up front so a clip change never blocks on
    a first-use frame grab — the source of the multi-second map lag."""
    portrait, landscape = tmp_path / "portrait", tmp_path / "landscape"
    portrait.mkdir()
    landscape.mkdir()
    (portrait / "a.mp4").write_text("x", encoding="utf-8")
    (portrait / "b.mp4").write_text("x", encoding="utf-8")
    (landscape / "c.mp4").write_text("x", encoding="utf-8")
    config = _hud_config(
        portrait_sources=str(portrait), landscape_sources=str(landscape),
        thumbnail_cache_dir=tmp_path / "thumbs",
    )
    warmed: list[tuple[str, object]] = []

    prewarm_thumbnails(
        config, thumbnailer=lambda path, cache: warmed.append((path, cache)), sleep_fn=lambda _s: None,
    )

    assert sorted(Path(p).name for p, _cache in warmed) == ["a.mp4", "b.mp4", "c.mp4"]
    assert all(cache == config.thumbnail_cache_dir for _p, cache in warmed)


def test_signal_hud_ready_writes_the_flag(tmp_path: Path):
    ready = tmp_path / "lock_hud_ready.txt"

    signal_hud_ready(ready)

    assert ready.exists()


def test_wait_for_hud_ready_returns_true_once_the_flag_appears(tmp_path: Path):
    """The flag is written a few polls in; the wait must catch it and report True
    without running out the full timeout."""
    ready = tmp_path / "lock_hud_ready.txt"
    ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3])

    def fake_sleep(_s: float) -> None:
        # The HUD finishes priming on the third poll.
        if not ready.exists() and fake_sleep.calls == 1:
            ready.write_text("ready", encoding="utf-8")
        fake_sleep.calls += 1

    fake_sleep.calls = 0

    assert wait_for_hud_ready(
        ready, timeout_s=5.0, poll_s=0.1, sleep_fn=fake_sleep, clock=lambda: next(ticks)
    ) is True


def test_wait_for_hud_ready_times_out_when_the_flag_never_appears(tmp_path: Path):
    """A HUD that never primes must not wedge startup — the wait lapses and
    reports False so the caller reveals anyway."""
    ready = tmp_path / "never.txt"
    ticks = iter([0.0, 0.5, 1.0, 1.5])

    assert wait_for_hud_ready(
        ready, timeout_s=1.0, poll_s=0.1, sleep_fn=lambda _s: None, clock=lambda: next(ticks)
    ) is False


def test_build_panels_indexes_each_side_and_carries_the_lock(tmp_path: Path):
    reset_group_index_cache()
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    current = _clip(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    sibling = _clip(media_root, metadata_root, "b", _i2v("redacted", "2"))
    config = _hud_config(
        portrait_sources=str(media_root / "portrait"),
        provider_media_root=media_root,
        provider_metadata_root=metadata_root,
    )

    portrait, landscape = build_panels(
        config,
        portrait_current=current, landscape_current="",
        portrait_locked=True, landscape_locked=False,
        portrait_filter="beta gamma", landscape_filter="",
    )

    assert portrait.side == "portrait" and portrait.locked is True
    assert portrait.action_siblings == [sibling]
    assert portrait.filter_query == "beta gamma"
    assert landscape.side == "landscape" and landscape.locked is False
    assert landscape.action_siblings == [] and landscape.seed_siblings == []
    assert landscape.filter_query == ""


def test_build_panels_threads_the_loop_kind_onto_the_panel(tmp_path: Path):
    """The loop kind comes off the shared state and must reach the panel so the
    map freezes — two seeds of one act make a real family to loop."""
    reset_group_index_cache()
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    _a = _clip(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    b = _clip(media_root, metadata_root, "b", _i2v("Alpha", "2"))
    config = _hud_config(
        portrait_sources=str(media_root / "portrait"),
        provider_media_root=media_root, provider_metadata_root=metadata_root,
    )

    portrait, _landscape = build_panels(
        config,
        portrait_current=b, landscape_current="",
        portrait_locked=False, landscape_locked=False,
        portrait_loop="seed",
    )

    assert portrait.active_loop == "seed"
    assert portrait.seed_siblings  # the other seed is on the row


def test_build_panels_threads_the_nav_anchor_onto_the_panel(tmp_path: Path):
    """The nav anchor comes off the shared state and must reach the panel so the
    map freezes on the clip navigation began from while the satellite plays a
    sibling."""
    reset_group_index_cache()
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    a = _clip(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    b = _clip(media_root, metadata_root, "b", _i2v("Alpha", "2"))
    config = _hud_config(
        portrait_sources=str(media_root / "portrait"),
        provider_media_root=media_root, provider_metadata_root=metadata_root,
    )

    # Navigation began from a; the satellite has since switched to its seed sibling b.
    portrait, _landscape = build_panels(
        config,
        portrait_current=b, landscape_current="",
        portrait_locked=False, landscape_locked=False,
        portrait_nav_anchor=a,
    )

    assert portrait.current == a       # frozen on the start clip
    assert portrait.playing == b       # the sibling on screen is lit


def test_build_panels_keeps_a_widened_seed_loop_wide_across_the_loose_family(tmp_path: Path):
    """The repro: widen the row, loop it, then let the loop auto-advance to a loose-
    family re-render that is not in the exact seed family.  The panel must stay
    widened and frozen on the anchor — not collapse with no loop shown."""
    reset_group_index_cache()
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    # a and a2 share the exact seed family (identical config, seed varied); b is the
    # same scene re-rendered with a render knob freed (a different model), so it joins
    # a's loose family but is its own exact family.
    a = _clip(media_root, metadata_root, "a", _i2v("Alpha", "1", image_seed="100"))
    a2 = _clip(media_root, metadata_root, "a2", _i2v("Alpha", "2", image_seed="101"))
    b_meta = _i2v("Alpha", "3", image_seed="200")
    b_meta["source_image"]["model"] = "Y Sweet"  # a render knob freed → a loose sibling
    b = _clip(media_root, metadata_root, "b", b_meta)
    config = _hud_config(
        portrait_sources=str(media_root / "portrait"),
        provider_media_root=media_root, provider_metadata_root=metadata_root,
    )

    # Widened around `a`; the loop has auto-advanced to `b`, a loose-family re-render
    # that is not in a's exact seed family {a, a2}.
    portrait, _landscape = build_panels(
        config,
        portrait_current=b, landscape_current="",
        portrait_locked=False, landscape_locked=False,
        portrait_loop="seed", portrait_widen_clip=a,
    )

    assert portrait.active_loop == "seed"            # the loop is still recognised
    assert portrait.current == a                     # frozen on the widened anchor
    assert portrait.playing == b                     # the widened member on screen
    assert set(portrait.seed_siblings) == {a2, b}    # the whole loose family, minus the anchor


# --- panel_thumbnails ---


def test_panel_thumbnails_caps_at_limit_and_skips_unreadable():
    def fake_thumbnailer(path: str, cache_dir) -> Path | None:
        if "bad" in path:
            return None  # unreadable clip
        return Path(cache_dir) / f"{Path(path).stem}.jpg"

    pairs = panel_thumbnails(
        ["a_good.mp4", "b_bad.mp4", "c_good.mp4", "d_good.mp4"],
        Path("cache"),
        limit=2,
        thumbnailer=fake_thumbnailer,
    )

    assert [path for path, _thumb in pairs] == ["a_good.mp4", "c_good.mp4"]
    assert all(isinstance(thumb, Path) for _path, thumb in pairs)


def test_panel_thumbnails_returns_empty_for_no_paths():
    assert panel_thumbnails([], Path("cache"), limit=4, thumbnailer=lambda *_: None) == []
