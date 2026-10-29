from __future__ import annotations

import json
import random
from pathlib import Path

from fun_time.config import load_config
from fun_time.content import WebProvider
from fun_time.media_metadata import mark_favorite
from fun_time.random_favs_browser import (
    FavEntry,
    build_manifest,
    choose_random,
    favorite_entries,
    resolve_profile_directory,
    write_manifest,
)
from fun_time.rfb_tab_page import TabTarget

_PROVIDERS = (WebProvider(marker="provider", gallery_url="https://example.com/image/{id}"),)


def _records(tmp_path: Path) -> Path:
    return tmp_path / "videos" / "metadata"


def _favorite_video(tmp_path: Path, relative: str, *, page: str = "") -> Path:
    video = tmp_path / "videos" / "videos" / relative
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    mark_favorite(video, _records(tmp_path), True, page=page)
    return video


def test_resolve_profile_directory_finds_named_profile(tmp_path: Path):
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Alex"},
                        "Profile 2": {"name": "Jane Doe"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_profile_directory(user_data_dir, "Jane Doe") == "Profile 2"



def test_choose_random_uses_requested_count():
    rng = random.Random(123)
    urls = [f"https://example.com/{i}" for i in range(20)]
    chosen = choose_random(urls, 10, rng)
    assert len(chosen) == 10
    assert len(set(chosen)) == 10


def test_the_manifest_names_the_chrome_profile_and_every_target(cfg_factory, tmp_path: Path):
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Jane Doe"}}}}),
        encoding="utf-8",
    )
    for number in (1, 2):
        _favorite_video(tmp_path, f"other/scene {number}.mp4", page=f"https://example.com/{number}")
    cfg_path = cfg_factory(
        {
            "regen": {"metadata_root": str(_records(tmp_path))},
            "random_favs_browser": {
                "enabled": True,
                "profile_name": "Jane Doe",
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )

    profile_name, targets = build_manifest(load_config(cfg_path), _PROVIDERS)
    assert profile_name == "Profile 2"
    assert sorted(target.url for target in targets) == [
        "https://example.com/1",
        "https://example.com/2",
    ]


def _decline_config(cfg_factory, tmp_path: Path, *, enabled=True, local_state=True,
                    profile_name="Jane Doe", urls=("https://example.com/1",)):
    """A config exercising one way the browser declines to launch."""
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    if local_state:
        (user_data_dir / "Local State").write_text(
            json.dumps({"profile": {"info_cache": {"Profile 2": {"name": profile_name}}}}),
            encoding="utf-8",
        )
    for number, url in enumerate(urls):
        _favorite_video(tmp_path, f"other/scene {number}.mp4", page=url)
    return cfg_factory(
        {
            "regen": {"metadata_root": str(_records(tmp_path))},
            "random_favs_browser": {
                "enabled": enabled,
                "profile_name": "Jane Doe",
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )


def test_a_disabled_browser_declines_to_launch(cfg_factory, tmp_path: Path):
    """random_favs_browser.enabled is a documented public config key (genaumacher
    reads it too), and turning it off must mean no profile and no tabs."""
    cfg_path = _decline_config(cfg_factory, tmp_path, enabled=False)
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("", [])


def test_a_missing_local_state_declines_to_launch(cfg_factory, tmp_path: Path):
    """No Chrome Local State file — a machine without that Chrome profile
    store — reads as 'no profile', not as a crash."""
    cfg_path = _decline_config(cfg_factory, tmp_path, local_state=False)
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("", [])


def test_an_unmatched_profile_name_declines_to_launch(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, profile_name="Nobody Here")
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("", [])


def test_an_empty_favs_list_opens_no_tabs_but_keeps_the_profile(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, urls=())
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("Profile 2", [])


def test_write_manifest_writes_profile_then_urls(tmp_path: Path):
    output_path = tmp_path / "manifest.txt"
    write_manifest(output_path, "Profile 2", ["https://example.com/1", "https://example.com/2"])
    assert output_path.read_text(encoding="utf-8") == (
        "Profile 2\n"
        "https://example.com/1\n"
        "https://example.com/2\n"
    )


# --- regenerate targets ---


def _regen_cfg(cfg_factory, tmp_path: Path) -> Path:
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Jane Doe"}}}}),
        encoding="utf-8",
    )
    return cfg_factory(
        {
            "random_favs_browser": {
                "enabled": True,
                "profile_name": "Jane Doe",
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
            "regen": {
                "media_root": str(tmp_path / "videos" / "videos" / "2D" / "AI"),
                "metadata_root": str(tmp_path / "videos" / "metadata"),
            },
        }
    )


# Where the library actually keeps an upscaled video, mirrored by its sidecar.
_UPSCALED = Path("2_outbox") / "upscaled_by_orientation" / "portrait"


def _write_sidecar(tmp_path: Path, name: str, metadata: dict, *, source: str = "provider",
                   page: str = "") -> Path:
    """A favorite video, and the record that says so beside what else *metadata* records."""
    video = tmp_path / "videos" / "videos" / "2D" / "AI" / _UPSCALED / source / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    sidecar = _records(tmp_path) / "2D" / "AI" / _UPSCALED / source / Path(name).with_suffix(".json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    mark_favorite(video, _records(tmp_path), True, page=page)
    return video


def test_build_manifest_opens_no_tab_for_a_favorite_no_provider_made(cfg_factory, tmp_path: Path):
    _write_sidecar(tmp_path, "clip_topaz.mp4", {"video": {"prompt": "P"}}, source="homemade")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert targets == []


def test_build_manifest_opens_another_sites_favorite_at_its_gallery(cfg_factory, tmp_path: Path):
    _write_sidecar(tmp_path, "abc_topaz.mp4", {"video": {"prompt": "P"}}, source="provider2",
                   page="https://example.net/image/abc")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)
    providers = (*_PROVIDERS, WebProvider(marker="provider2", gallery_url="https://example.net/image/{id}"))

    _, targets = build_manifest(load_config(cfg_path), providers)

    assert [target.url for target in targets] == ["https://example.net/image/abc"]


def test_build_manifest_opens_a_favorite_with_no_recorded_prompt_at_its_gallery(cfg_factory, tmp_path: Path):
    stamped_by_evolver = {
        "video": {"type": "short", "action": "Alpha", "duration_seconds": 5.0},
        "provenance": {"upscale": {"app": "evolver"}},
        "watch": {"weight": 1.0},
    }
    _write_sidecar(tmp_path, "abc_topaz.mp4", stamped_by_evolver, page="https://example.com/image/abc")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert [target.url for target in targets] == ["https://example.com/image/abc"]


def test_build_manifest_targets_the_regenerate_page_when_metadata_exists(cfg_factory, tmp_path: Path):
    """A dead gallery link becomes the prefilled Provider generate page."""
    video = _write_sidecar(tmp_path, "abc_topaz.mp4", {"video": {"prompt": "A PROMPT"}},
                           page="https://example.com/image/abc")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert len(targets) == 1
    assert targets[0].url.startswith("https://example.com/video#ft=")
    assert targets[0].label == "https://example.com/image/abc"
    # The tab plays the clip you are deciding whether to recreate.
    assert targets[0].video_path == str(video)


def test_build_manifest_plays_the_original_not_the_upscale(cfg_factory, tmp_path: Path):
    """The upscale is hundreds of MB of HEVC; its original is a few MB of H.264."""
    _write_sidecar(tmp_path, "abc_topaz.mp4", {"video": {"prompt": "P"}}, page="https://example.com/image/abc")
    original = tmp_path / "videos" / "videos" / "2D" / "AI" / "1_sorted" / "provider" / "portrait" / "abc.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert targets[0].video_path == str(original)


def test_build_manifest_falls_back_to_the_gallery_link_without_metadata(cfg_factory, tmp_path: Path):
    """A record saying nothing but that it is a favorite leaves the stored link untouched."""
    video = _favorite_video(tmp_path, "media/provider2/abc.mp4", page="https://example.net/image/abc")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert targets == [
        TabTarget(
            url="https://example.net/image/abc",
            label="https://example.net/image/abc",
            video_path=str(video),
        )
    ]


def test_build_manifest_labels_a_gallery_less_fav_with_its_filename(cfg_factory, tmp_path: Path):
    _write_sidecar(tmp_path, "def_topaz.mp4", {"video": {"prompt": "P"}})
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert targets[0].label == "def_topaz.mp4"


def test_build_manifest_drops_favs_with_nowhere_to_go(cfg_factory, tmp_path: Path):
    """A non-Provider local file with no gallery link yields no tab at all."""
    _favorite_video(tmp_path, "media/other/abc.mp4")
    cfg_path = _regen_cfg(cfg_factory, tmp_path)

    _, targets = build_manifest(load_config(cfg_path), _PROVIDERS)

    assert targets == []


# --- the favorites the browser opens ---


def test_the_favorites_are_the_videos_their_records_mark_with_the_pages_they_came_from(tmp_path: Path):
    video = _favorite_video(tmp_path, "other/scene one.mp4", page="https://example.com/1")

    assert favorite_entries(_records(tmp_path)) == [
        FavEntry(local_path=str(video), web_url="https://example.com/1")]


def test_with_no_records_folder_there_are_no_favorites_to_open():
    assert favorite_entries(None) == []
