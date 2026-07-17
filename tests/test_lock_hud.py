from __future__ import annotations

import json
import textwrap
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.lock_hud import (
    HudAppConfig,
    build_hud_panel,
    build_panels,
    hud_overlays_visible,
    load_hud_app_config,
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
    map stays up (and topmost) while paused."""
    assert hud_overlays_visible(loading_active=True) is False
    assert hud_overlays_visible(loading_active=False) is True


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
    wide = build_hud_panel("portrait", locked=False, current=CUR, index=index, widen=True)

    assert narrow.seed_siblings == [S1]                 # exact family only
    assert set(wide.seed_siblings) == {S1, other}       # widened to the loose family
    assert wide.current == CUR                          # the clip on screen is unchanged


def test_a_group_of_one_does_not_freeze_the_map():
    """A "loop" over a family of one is really a lock, so there is nothing to
    freeze — the map stays anchored on the live clip and reports no loop."""
    index = _index(current=CUR)

    panel = build_hud_panel("portrait", locked=False, current=CUR, index=index, loop_axis="seed")

    assert panel.active_loop == ""
    assert panel.current == CUR
    assert panel.playing == CUR


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
