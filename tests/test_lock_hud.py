from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from funestra.hud_title import F_MODE_LABEL
from funestra.video_groups import (
    GroupIndex,
    VideoEntry,
)
from funestra.video_groups import normalize_path_key as K

from fun_time.lock_hud import (
    SatelliteInputs,
    build_hud,
    build_satellite_hud,
    cell_path,
    hud_thumbnails,
    locate_cell,
    nav_map_cells,
    navigate_cell,
    prime_group_indexes,
)
from fun_time.media_metadata import (
    cached_group_index,
    metadata_path_for,
)

CUR = "C:/vids/current.mp4"
A1 = "C:/vids/action1.mp4"
A2 = "C:/vids/action2.mp4"
S1 = "C:/vids/seed1.mp4"


def _entries(*videos: VideoEntry) -> dict[str, VideoEntry]:
    return {K(video.path): video for video in videos}


def _index(*, current: str, action_sibs=(), seed_sibs=()) -> GroupIndex:
    action_all = sorted([current, *action_sibs])
    seed_all = sorted([current, *seed_sibs])
    acts = {current: "Alpha", **{p: f"Act{i}" for i, p in enumerate(action_sibs)},
            **dict.fromkeys(seed_sibs, "Alpha")}
    seeds = {p: ("S", str(i)) for i, p in enumerate(seed_all)}
    return GroupIndex(
        entries=_entries(
            *(VideoEntry(p, action=acts[p], action_key="A" if p in action_all else None,
                      seed_key=seeds.get(p))
              for p in (current, *action_sibs, *seed_sibs)),
        ),
        action_items={"A": action_all},
        seed_items={"S": seed_all},
    )


def _hud(funestra: str = "portrait", *, index, **satellite_fields):
    return build_hud(SatelliteInputs(funestra, **satellite_fields), index=index)


def test_hud_gathers_action_and_seed_siblings_and_labels_the_lock():
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    hud = _hud("portrait", locked=True, current=CUR, index=index)

    assert hud.funestra == "portrait"
    assert hud.locked is True
    assert hud.title == "Locked · Shuffle"
    assert hud.current == CUR
    assert hud.action_siblings == sorted([A1, A2])
    assert hud.seed_siblings == [S1]


def test_hud_carries_axis_labels_for_the_map():
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    hud = _hud("portrait", locked=True, current=CUR, index=index)

    assert hud.current_action == "Alpha"
    assert hud.action_lines == ("Act0", "Act1")
    assert len(hud.action_lines) == len(hud.action_siblings)


def test_hud_labels_an_unlocked_satellite():
    index = _index(current=CUR, action_sibs=[A1])

    hud = _hud("landscape", locked=False, current=CUR, index=index)

    assert hud.locked is False
    assert hud.title == "Unlocked · Shuffle"
    assert hud.action_siblings == [A1]


def test_hud_without_a_current_video_has_no_siblings():
    index = _index(current=CUR, action_sibs=[A1], seed_sibs=[S1])

    hud = _hud("portrait", locked=False, current="", index=index)

    assert hud.action_siblings == []
    assert hud.seed_siblings == []


def test_hud_carries_the_active_filter():
    index = _index(current=CUR)

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index, filter_query="beta gamma"
    )

    assert hud.filter_query == "beta gamma"
    assert _hud("portrait", locked=False, current=CUR, index=index).filter_query == ""


def test_without_a_loop_the_map_anchors_on_the_live_video():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud("portrait", locked=False, current=CUR, index=index)

    assert hud.active_loop == ""
    assert hud.current == CUR
    assert hud.playing == CUR


def test_a_seed_loop_freezes_the_map_on_the_video_the_loop_started_on():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=S1, index=index, loop_axis="seed", nav_map_anchor=S1,
    )

    assert hud.active_loop == "seed"
    assert hud.current == S1
    assert hud.playing == S1
    assert hud.seed_siblings == [CUR]


def test_a_seed_loop_keeps_its_anchor_as_the_loop_advances():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index, loop_axis="seed", nav_map_anchor=S1,
    )

    assert hud.current == S1
    assert hud.playing == CUR
    assert hud.seed_siblings == [CUR]


A = "C:/vids/anchor.mp4"
AY = "C:/vids/anchor_beta.mp4"
N = "C:/vids/nextseed.mp4"
NX = "C:/vids/nextseed_zeta.mp4"


def _two_seed_index() -> GroupIndex:
    return GroupIndex(
        entries=_entries(
            VideoEntry(A, action="Alpha", action_key="GA", seed_key=("F", "1")),
            VideoEntry(AY, action="Beta", action_key="GA", seed_key=("F", "1")),
            VideoEntry(N, action="Alpha", action_key="GN", seed_key=("F", "2")),
            VideoEntry(NX, action="Zeta", action_key="GN", seed_key=("F", "2")),
        ),
        action_items={"GA": sorted([A, AY]), "GN": sorted([N, NX])},
        seed_items={"F": sorted([A, AY, N, NX])},
    )


def test_a_seed_loops_column_belongs_to_the_playing_seed():
    index = _two_seed_index()

    hud = _hud(
        "portrait", locked=False, current=N, index=index, loop_axis="seed", nav_map_anchor=A,
    )

    assert hud.current == A
    assert hud.playing == N
    assert hud.action_siblings == [NX]
    assert hud.action_lines == ("Zeta",)


def test_at_the_loops_head_the_column_is_the_corners_own():
    index = _two_seed_index()

    hud = _hud(
        "portrait", locked=False, current=A, index=index, loop_axis="seed", nav_map_anchor=A,
    )

    assert hud.action_siblings == [AY]
    assert hud.action_lines == ("Beta",)


def test_a_map_held_after_the_loop_keeps_the_column_on_the_playing_seed():
    index = _two_seed_index()

    hud = _hud(
        "portrait", locked=False, current=N, index=index, loop_axis="", nav_map_anchor=A,
    )

    assert hud.current == A
    assert hud.playing == N
    assert hud.action_siblings == [NX]


def test_a_nav_frozen_maps_column_follows_the_selected_seed():
    index = _two_seed_index()

    hud = _hud(
        "portrait", locked=False, current=N, index=index, nav_anchor=A,
    )

    assert hud.current == A
    assert hud.playing == N
    assert hud.action_siblings == [NX]


def test_a_video_down_the_column_keeps_the_corners_column():
    index = _two_seed_index()

    held = _hud(
        "portrait", locked=False, current=AY, index=index, nav_map_anchor=A,
    )
    frozen = _hud(
        "portrait", locked=False, current=AY, index=index, nav_anchor=A,
    )

    for hud in (held, frozen):
        assert hud.current == A
        assert hud.playing == AY
        assert hud.action_siblings == [AY]


def test_an_action_loop_anchors_on_its_start_video_and_marks_the_playing_action():
    index = _index(current=CUR, action_sibs=[A1])

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index, loop_axis="action", nav_map_anchor=CUR,
    )

    assert hud.active_loop == "action"
    assert hud.current == CUR
    assert hud.playing == CUR
    assert hud.action_siblings == [A1]


def _twin_index(current: str, twin: str) -> GroupIndex:
    items = sorted([current, twin])
    return GroupIndex(
        entries=_entries(*(VideoEntry(p, action="Alpha", action_key="A") for p in items)),
        action_items={"A": items},
        seed_items={},
    )


def test_a_running_action_loop_draws_every_video_it_cycles():
    index = _twin_index(CUR, A1)

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index, loop_axis="action", nav_map_anchor=CUR,
    )

    assert hud.action_siblings == [A1]
    assert hud.action_count == 2


def test_a_looped_action_column_lights_the_twin_actually_playing():
    index = _twin_index(CUR, A1)

    hud = _hud(
        "portrait", locked=False, current=A1, index=index, loop_axis="action", nav_map_anchor=CUR,
    )

    assert hud.current == CUR
    assert hud.playing == A1


def test_the_browse_map_still_collapses_same_act_twins():
    index = _twin_index(CUR, A1)

    hud = _hud("portrait", locked=False, current=CUR, index=index)

    assert hud.action_siblings == []
    assert hud.action_count == 1


def test_ending_a_loop_leaves_the_map_hanging_where_it_was():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=S1, index=index, loop_axis="", nav_map_anchor=CUR,
    )

    assert hud.active_loop == ""
    assert hud.current == CUR
    assert hud.playing == S1
    assert hud.seed_siblings == [S1]


def test_a_map_anchor_is_let_go_once_the_video_leaves_the_map():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current="C:/vids/unrelated.mp4", index=index, nav_map_anchor=CUR,
    )

    assert hud.current == "C:/vids/unrelated.mp4"


def test_ending_a_widened_loop_keeps_the_row_wide():
    near = "C:/vids/near.mp4"
    index = GroupIndex(
        entries=_entries(
            VideoEntry(CUR, action="Alpha", action_key="g1", seed_key=("S", "0"),
                      scene_tags=frozenset({"a", "b", "c"})),
            VideoEntry(near, action="Alpha", action_key="g2",
                      scene_tags=frozenset({"a", "b", "d"})),
        ),
        action_items={"g1": [CUR], "g2": [near]},
        seed_items={"S": [CUR]},
    )

    hud = _hud(
        "portrait", locked=False, current=near, index=index,
        loop_axis="", nav_map_anchor=CUR, widen_video=CUR,
    )

    assert hud.current == CUR
    assert hud.seed_siblings == [near]


def test_the_title_holds_every_state_the_satellite_is_in():
    index = _index(current=CUR, seed_sibs=[S1])

    looping = _hud(
        "portrait", locked=False, current=CUR, index=index,
        loop_axis="seed", nav_map_anchor=CUR, latest=True, filter_query="beta gamma",
    )
    locked = _hud("portrait", locked=True, current=CUR, index=index)

    assert looping.title == "Looping seeds · Latest · beta gamma"
    assert locked.title == "Locked · Shuffle"


def test_the_title_says_when_f_mode_is_narrowing_the_library():
    index = _index(current=CUR, seed_sibs=[S1])

    on = _hud("portrait", locked=False, current=CUR, index=index, favorites_filter=True)
    off = _hud("portrait", locked=False, current=CUR, index=index)

    assert on.title == f"Unlocked · Shuffle · {F_MODE_LABEL}"
    assert F_MODE_LABEL not in off.title


def test_f_mode_sits_before_the_filter_so_the_filter_stays_last():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index,
        favorites_filter=True, filter_query="beta gamma",
    )

    assert hud.title == f"Unlocked · Shuffle · {F_MODE_LABEL} · beta gamma"


def test_the_title_names_the_axis_that_is_looping():
    seed_index = _index(current=CUR, seed_sibs=[S1])
    action_index = _index(current=CUR, action_sibs=[A1])

    seeds = _hud("portrait", locked=False, current=CUR, index=seed_index,
                            loop_axis="seed", nav_map_anchor=CUR)
    actions = _hud("portrait", locked=False, current=CUR, index=action_index,
                              loop_axis="action", nav_map_anchor=CUR)

    assert seeds.title.startswith("Looping seeds")
    assert actions.title.startswith("Looping actions")


def test_the_lock_word_stands_down_while_a_loop_runs():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index, loop_axis="seed", nav_map_anchor=CUR,
    )

    assert "Locked" not in hud.title
    assert "Unlocked" not in hud.title
    assert "Shuffle" in hud.title


def test_a_lock_taken_inside_a_loop_joins_the_line_instead_of_replacing_it():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=True, current=CUR, index=index, loop_axis="seed", nav_map_anchor=CUR,
    )

    assert hud.title == "Looping seeds · Locked · Shuffle"


def test_a_lock_inside_a_loop_keeps_the_loops_chrome_and_rings_the_held_video():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=True, current=S1, index=index, loop_axis="seed", nav_map_anchor=CUR,
    )

    assert hud.locked is True
    assert hud.active_loop == "seed"
    assert hud.current == CUR
    assert hud.playing == S1


def test_the_title_says_looping_without_counting():
    index = _index(current=CUR, seed_sibs=[S1, "C:/vids/seed2.mp4"])

    hud = _hud(
        "portrait", locked=False, current=CUR, index=index, loop_axis="seed", nav_map_anchor=CUR,
    )

    assert "Looping seeds" in hud.title
    assert "3" not in hud.title


def test_the_hud_counts_the_seeds_and_actions_the_map_stands_for():
    index = _index(current=CUR, seed_sibs=[S1, "C:/vids/seed2.mp4"], action_sibs=[A1])

    hud = _hud("portrait", locked=False, current=CUR, index=index)

    assert hud.seed_count == 3
    assert hud.action_count == 2


def test_a_loop_without_a_recorded_anchor_still_freezes_the_map():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=S1, index=index, loop_axis="seed", nav_map_anchor="",
    )

    assert hud.active_loop == "seed"
    assert hud.current == CUR
    assert hud.playing == S1


def test_widen_grows_the_seed_row_with_the_nearest_videos():
    other = "C:/vids/other.mp4"
    index = GroupIndex(
        entries=_entries(
            VideoEntry(CUR, action="Alpha", action_key="g1", seed_key=("S", "0"),
                      scene_tags=frozenset({"a", "b", "c"})),
            VideoEntry(S1, action="Alpha", action_key="g1", seed_key=("S", "1"),
                      scene_tags=frozenset({"a", "b", "c"})),
            VideoEntry(other, action="Alpha", action_key="g2",
                      scene_tags=frozenset({"a", "b", "d"})),
        ),
        action_items={"g1": sorted([CUR, S1]), "g2": [other]},
        seed_items={"S": sorted([CUR, S1])},
    )

    narrow = _hud("portrait", locked=False, current=CUR, index=index)
    wide = _hud("portrait", locked=False, current=CUR, index=index, widen_video=CUR)

    assert narrow.seed_siblings == [S1]
    assert set(wide.seed_siblings) == {S1, other}
    assert wide.current == CUR


def test_widen_off_a_loop_resets_once_its_anchor_video_leaves_the_screen():
    other = "C:/vids/other.mp4"
    index = GroupIndex(
        entries=_entries(
            VideoEntry(CUR, action="Alpha", action_key="g1", seed_key=("S", "0")),
            VideoEntry(S1, action="Alpha", action_key="g1", seed_key=("S", "1")),
            VideoEntry(other, action="Alpha", action_key="g2"),
        ),
        action_items={"g1": sorted([CUR, S1]), "g2": [other]},
        seed_items={"S": sorted([CUR, S1])},
    )

    hud = _hud("portrait", locked=False, current=other, index=index, widen_video=CUR)

    assert hud.seed_siblings == []


def test_a_widened_seed_loop_stays_wide_and_frozen_across_the_widened_pool():
    x, x2, y, z = "C:/v/x.mp4", "C:/v/x2.mp4", "C:/v/y.mp4", "C:/v/z.mp4"
    seeds = {x: ("F1", "0"), x2: ("F1", "1"), y: ("F2", "0"), z: ("F3", "0")}
    index = GroupIndex(
        entries=_entries(
            *(VideoEntry(p, action="Alpha", action_key="scene", seed_key=seeds[p],
                        scene_tags=frozenset({"a", "b", "c"}))
              for p in (x, x2, y, z)),
        ),
        action_items={"scene": sorted([x, x2, y, z])},
        seed_items={"F1": sorted([x, x2]), "F2": [y], "F3": [z]},
    )

    hud = _hud(
        "portrait", locked=False, current=y, index=index, loop_axis="seed", widen_video=x,
    )

    assert hud.active_loop == "seed"
    assert hud.current == x
    assert hud.playing == y
    assert set(hud.seed_siblings) == {x2, y, z}


def test_a_nav_frozen_map_shows_the_exact_family_the_keys_walk():
    other = "C:/vids/other.mp4"
    index = GroupIndex(
        entries=_entries(
            VideoEntry(CUR, action="Alpha", action_key="g1", seed_key=("S", "0"),
                      scene_tags=frozenset({"a", "b", "c"})),
            VideoEntry(S1, action="Alpha", action_key="g1", seed_key=("S", "1"),
                      scene_tags=frozenset({"a", "b", "c"})),
            VideoEntry(other, action="Alpha", action_key="g2",
                      scene_tags=frozenset({"a", "b", "d"})),
        ),
        action_items={"g1": sorted([CUR, S1]), "g2": [other]},
        seed_items={"S": sorted([CUR, S1])},
    )

    hud = _hud("portrait", locked=False, current=S1, index=index, nav_anchor=CUR, widen_video=S1)

    assert hud.current == CUR
    assert hud.playing == S1
    assert hud.seed_siblings == [S1]
    assert nav_map_cells(index, CUR)[0] == hud.seed_siblings


def test_a_non_widened_seed_loop_ignores_a_cleared_widen_anchor():
    x, x2, y = "C:/v/x.mp4", "C:/v/x2.mp4", "C:/v/y.mp4"
    seeds = {x: ("F1", "0"), x2: ("F1", "1"), y: ("F2", "0")}
    index = GroupIndex(
        entries=_entries(
            *(VideoEntry(p, action="Alpha", action_key="scene", seed_key=seeds[p],
                        scene_tags=frozenset({"a", "b", "c"}))
              for p in (x, x2, y)),
        ),
        action_items={"scene": sorted([x, x2, y])},
        seed_items={"F1": sorted([x, x2]), "F2": [y]},
    )

    hud = _hud(
        "portrait", locked=False, current=x2, index=index, loop_axis="seed", widen_video="",
    )

    assert hud.active_loop == "seed"
    assert hud.current == x
    assert set(hud.seed_siblings) == {x2}


def test_a_group_of_one_does_not_freeze_the_map():
    index = _index(current=CUR)

    hud = _hud("portrait", locked=False, current=CUR, index=index, loop_axis="seed")

    assert hud.active_loop == ""
    assert hud.current == CUR
    assert hud.playing == CUR


def test_nav_anchor_freezes_the_map_on_the_video_navigation_began_from():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud("portrait", locked=False, current=S1, index=index, nav_anchor=CUR)

    assert hud.current == CUR
    assert hud.playing == S1
    assert hud.seed_siblings == [S1]
    assert hud.active_loop == ""


def test_nav_anchor_lights_a_selected_action_cell():
    index = _index(current=CUR, action_sibs=[A1])

    hud = _hud("portrait", locked=False, current=A1, index=index, nav_anchor=CUR)

    assert hud.current == CUR
    assert hud.playing == A1
    assert hud.action_siblings == [A1]


def test_nav_anchor_equal_to_the_live_video_is_the_ordinary_map():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud("portrait", locked=False, current=CUR, index=index, nav_anchor=CUR)

    assert hud.current == CUR
    assert hud.playing == CUR
    assert hud.seed_siblings == [S1]


def test_nav_anchor_re_homes_once_the_video_drifts_off_the_map():
    index = _index(current=CUR, seed_sibs=[S1])
    elsewhere = "C:/vids/elsewhere.mp4"

    hud = _hud("portrait", locked=False, current=elsewhere, index=index, nav_anchor=CUR)

    assert hud.current == elsewhere
    assert hud.playing == elsewhere
    assert hud.seed_siblings == []


def test_a_loop_takes_precedence_over_a_stale_nav_anchor():
    index = _index(current=CUR, seed_sibs=[S1])

    hud = _hud(
        "portrait", locked=False, current=S1, index=index, loop_axis="seed", nav_anchor="C:/vids/other.mp4"
    )

    assert hud.active_loop == "seed"
    assert hud.current == CUR


def test_navigate_from_the_corner_steps_onto_the_action_column():
    corner = ("corner", 0)
    assert navigate_cell(corner, "down", action_count=2) == ("action", 0)
    assert navigate_cell(corner, "up", action_count=2) == ("action", 1)


def test_navigate_walks_the_action_column_and_wraps_past_its_end():
    assert navigate_cell(("action", 0), "down", action_count=2) == ("action", 1)
    assert navigate_cell(("action", 1), "down", action_count=2) == ("corner", 0)
    assert navigate_cell(("action", 1), "up", action_count=2) == ("action", 0)
    assert navigate_cell(("action", 0), "up", action_count=2) == ("corner", 0)


def test_navigate_from_a_seed_cell_is_a_no_op():
    assert navigate_cell(("seed", 1), "down", action_count=2) == ("seed", 1)
    assert navigate_cell(("seed", 1), "up", action_count=2) == ("seed", 1)


def test_navigate_along_the_row_is_no_longer_a_direction():
    corner = ("corner", 0)
    assert navigate_cell(corner, "right", action_count=2) == corner
    assert navigate_cell(corner, "left", action_count=2) == corner


def test_navigate_onto_an_empty_column_stays_put():
    assert navigate_cell(("corner", 0), "down", action_count=0) == ("corner", 0)


def test_locate_cell_matches_the_corner_a_seed_or_an_action():
    seeds = [S1, "C:/vids/seed2.mp4"]
    actions = [A1]
    assert locate_cell(CUR, CUR, seeds, actions) == ("corner", 0)
    assert locate_cell(S1, CUR, seeds, actions) == ("seed", 0)
    assert locate_cell("C:/vids/seed2.mp4", CUR, seeds, actions) == ("seed", 1)
    assert locate_cell(A1, CUR, seeds, actions) == ("action", 0)


def test_locate_cell_is_none_when_the_video_is_off_the_map():
    assert locate_cell("C:/vids/elsewhere.mp4", CUR, [S1], [A1]) is None


def test_locate_cell_matches_case_insensitively():
    assert locate_cell("C:/VIDS/SEED1.MP4", CUR, [S1], []) == ("seed", 0)


def test_cell_path_reads_the_video_at_a_cell():
    seeds = [S1, "C:/vids/seed2.mp4"]
    actions = [A1, A2]
    assert cell_path(("corner", 0), CUR, seeds, actions) == CUR
    assert cell_path(("seed", 1), CUR, seeds, actions) == "C:/vids/seed2.mp4"
    assert cell_path(("action", 1), CUR, seeds, actions) == A2


def test_cell_path_is_empty_for_an_out_of_range_cell():
    assert cell_path(("seed", 5), CUR, [S1], []) == ""
    assert cell_path(("action", 0), CUR, [S1], []) == ""


def test_hud_map_cells_lists_the_drawn_seed_and_action_videos():
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    seeds, actions = nav_map_cells(index, CUR)

    assert seeds == [S1]
    assert actions == sorted([A1, A2])


def test_hud_map_cells_caps_each_axis_at_the_draw_limit():
    seed_sibs = [f"C:/vids/seed{i}.mp4" for i in range(9)]
    index = _index(current=CUR, seed_sibs=seed_sibs)

    seeds, _actions = nav_map_cells(index, CUR, seed_limit=6, action_limit=4)

    assert len(seeds) == 6


def _i2v(action: str, video_seed: str, image_seed: str = "100") -> dict:
    return {
        "video": {
            "prompt": "a scene", "model": "Realism", "action": action, "labels": [action],
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


def _video(media_root: Path, metadata_root: Path, name: str, meta: dict) -> str:
    video = media_root / "portrait" / f"{name}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("x", encoding="utf-8")
    sidecar = metadata_path_for(video, metadata_root)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    return str(video)


def test_prime_group_indexes_builds_both_satellites_up_front(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    _video(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    sources = str(media_root / "portrait")
    prime_group_indexes((sources, sources), metadata_root)

    index = cached_group_index(sources, paths_supplier=list, metadata_root=metadata_root, must_contain=None)
    assert index.entries


def test_the_first_hud_after_priming_lists_no_folder(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    current = _video(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    sources = str(media_root / "portrait")
    prime_group_indexes((sources, ""), metadata_root)

    with patch("fun_time.lock_hud.collect_video_files") as listed:
        hud = build_satellite_hud(SatelliteInputs("portrait", sources=sources, current=current), metadata_root)

    listed.assert_not_called()
    assert hud.current == current


def test_a_satellites_hud_indexes_its_own_satellite_and_carries_the_lock(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    current = _video(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    sibling = _video(media_root, metadata_root, "b", _i2v("Beta", "2"))
    sources = str(media_root / "portrait")

    portrait = build_satellite_hud(
        SatelliteInputs("portrait", sources=sources, current=current, locked=True,
                   filter_query="beta gamma"), metadata_root)
    landscape = build_satellite_hud(SatelliteInputs("landscape"), metadata_root)

    assert portrait.funestra == "portrait"
    assert portrait.locked is True
    assert portrait.action_siblings == [sibling]
    assert portrait.filter_query == "beta gamma"
    assert landscape.funestra == "landscape"
    assert landscape.locked is False
    assert landscape.action_siblings == []
    assert landscape.seed_siblings == []
    assert landscape.filter_query == ""


def test_a_satellites_hud_says_f_mode_only_when_that_satellite_is_in_it(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    current = _video(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    sources = str(media_root / "portrait")

    portrait = build_satellite_hud(
        SatelliteInputs("portrait", sources=sources, current=current, favorites_filter=True),
        metadata_root)
    landscape = build_satellite_hud(
        SatelliteInputs("landscape", sources=sources, current=current), metadata_root)

    assert F_MODE_LABEL in portrait.title
    assert F_MODE_LABEL not in landscape.title
    assert (portrait.favorites_filter, landscape.favorites_filter) == (True, False)


def test_a_satellites_hud_carries_the_loop_kind(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    _a = _video(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    b = _video(media_root, metadata_root, "b", _i2v("Alpha", "2"))
    sources = str(media_root / "portrait")

    portrait = build_satellite_hud(
        SatelliteInputs("portrait", sources=sources, current=b, loop_axis="seed"),
        metadata_root)

    assert portrait.active_loop == "seed"
    assert portrait.seed_siblings


def test_a_satellites_hud_carries_the_nav_anchor(tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    a = _video(media_root, metadata_root, "a", _i2v("Alpha", "1"))
    b = _video(media_root, metadata_root, "b", _i2v("Alpha", "2"))
    sources = str(media_root / "portrait")

    portrait = build_satellite_hud(
        SatelliteInputs("portrait", sources=sources, current=b, nav_anchor=a),
        metadata_root)

    assert portrait.current == a
    assert portrait.playing == b


def test_a_satellites_hud_keeps_a_widened_seed_loop_wide_across_the_loose_family(
        tmp_path: Path):
    media_root, metadata_root = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    a = _video(media_root, metadata_root, "a", _i2v("Alpha", "1", image_seed="100"))
    a2 = _video(media_root, metadata_root, "a2", _i2v("Alpha", "2", image_seed="101"))
    b_meta = _i2v("Alpha", "3", image_seed="200")
    b_meta["source_image"]["model"] = "Y Sweet"
    b = _video(media_root, metadata_root, "b", b_meta)
    sources = str(media_root / "portrait")

    portrait = build_satellite_hud(
        SatelliteInputs("portrait", sources=sources, current=b, loop_axis="seed", widen_video=a),
        metadata_root)

    assert portrait.active_loop == "seed"
    assert portrait.current == a
    assert portrait.playing == b
    assert set(portrait.seed_siblings) == {a2, b}


def test_hud_thumbnails_caps_at_limit_and_skips_unreadable():
    def fake_thumbnailer(path: str, cache_dir) -> Path | None:
        if "bad" in path:
            return None
        return Path(cache_dir) / f"{Path(path).stem}.jpg"

    pairs = hud_thumbnails(
        ["a_good.mp4", "b_bad.mp4", "c_good.mp4", "d_good.mp4"],
        Path("cache"),
        limit=2,
        thumbnailer=fake_thumbnailer,
    )

    assert [path for path, _thumb in pairs] == ["a_good.mp4", "c_good.mp4"]
    assert all(isinstance(thumb, Path) for _path, thumb in pairs)


def test_a_hud_with_no_videos_in_it_shows_no_thumbnails():
    assert hud_thumbnails([], Path("cache"), limit=4, thumbnailer=lambda *_: None) == []


def _column_index(**actions: str) -> GroupIndex:
    paths = {name: f"C:/vids/{name}.mp4" for name in actions}
    return GroupIndex(
        entries=_entries(*(VideoEntry(path=paths[name], action=action, action_key="G")
                           for name, action in actions.items())),
        action_items={"G": sorted(paths.values())},
        seed_items={},
    )


def test_a_video_with_no_action_has_a_row_in_the_column_with_no_action_name():
    index = _column_index(words="Alpha", made="")

    hud = _hud("portrait", locked=False, current="C:/vids/words.mp4", index=index)

    assert hud.action_siblings == ["C:/vids/made.mp4"]
    assert hud.action_lines == ("",)


def test_a_video_with_no_action_sees_the_others_in_its_column():
    index = _column_index(words="Alpha", made="")

    hud = _hud("portrait", locked=False, current="C:/vids/made.mp4", index=index)

    assert hud.action_siblings == ["C:/vids/words.mp4"]
    assert hud.action_lines == ("Alpha",)


def test_several_videos_with_no_action_share_one_row_as_any_action_does():
    index = _column_index(words="Alpha", made_one="", made_two="")

    hud = _hud("portrait", locked=False, current="C:/vids/words.mp4", index=index)

    assert hud.action_siblings == ["C:/vids/made_one.mp4"]


def test_a_video_with_no_action_has_no_row_for_its_own_blank():
    index = _column_index(made_one="", made_two="")

    hud = _hud("portrait", locked=False, current="C:/vids/made_one.mp4", index=index)

    assert hud.action_siblings == []
