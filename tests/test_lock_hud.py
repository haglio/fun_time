from __future__ import annotations

import json
import textwrap
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.lock_hud import (
    HudAppConfig,
    build_hud_panel,
    build_panels,
    load_hud_app_config,
    overlay_rect,
    panel_thumbnails,
    primary_sound_label,
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


def test_panel_gathers_action_and_seed_siblings_and_labels_the_lock():
    index = _index(current=CUR, action_sibs=[A1, A2], seed_sibs=[S1])

    panel = build_hud_panel("portrait", locked=True, current=CUR, index=index)

    assert panel.side == "portrait"
    assert panel.locked is True
    assert panel.lock_label == "Locked"
    assert panel.action_siblings == sorted([A1, A2])
    assert panel.seed_siblings == [S1]


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


# --- primary_sound_label ---


def test_primary_sound_label_reports_the_level_when_audible():
    assert primary_sound_label(80, muted=False) == "VOL 80"
    assert primary_sound_label(0, muted=False) == "VOL 0"


def test_primary_sound_label_reports_mute_over_the_level():
    """A mute leaves the level alone, so the level is meaningless while silenced."""
    assert primary_sound_label(80, muted=True) == "MUTED"


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
    sidecar = metadata_path_for(video, media_root, metadata_root)
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
    )
    base.update(overrides)
    return HudAppConfig(**base)


def test_build_panels_indexes_each_side_and_carries_the_lock(tmp_path: Path):
    reset_group_index_cache()
    media_root, metadata_root = tmp_path / "AI", tmp_path / "metadata"
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
