from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

from fun_time.config import load_config
from fun_time.content import WebProvider
from fun_time.gallery import Gallery, gallery_database_in
from fun_time.lock import a_lock_holds
from fun_time.media_metadata import mark_favorite
from fun_time.random_faves_browser import (
    FavEntry,
    build_manifest,
    choose_random,
    favorite_entries,
    resolve_profile_directory,
    write_manifest,
)
from fun_time.rfb_tab_page import TabTarget, render_tab_page
from fun_time.shared_state import BridgeState
from tests.made_up_gallery import a_row, made_up_origenerator
from tests.test_windows_bridge_dispatch_loop import make_config

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
            "random_faves_browser": {
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


@dataclass(frozen=True)
class DeclineBrowser:
    enabled: bool = True
    local_state: bool = True
    profile_name: str = "Jane Doe"
    urls: tuple = ("https://example.com/1",)


def _decline_config(cfg_factory, tmp_path: Path, browser: DeclineBrowser | None = None):
    browser = browser or DeclineBrowser()
    enabled, local_state, profile_name, urls = (
        browser.enabled, browser.local_state, browser.profile_name, browser.urls)
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
            "random_faves_browser": {
                "enabled": enabled,
                "profile_name": "Jane Doe",
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )


def test_a_disabled_browser_declines_to_launch(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, DeclineBrowser(enabled=False))
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("", [])


def test_a_missing_local_state_declines_to_launch(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, DeclineBrowser(local_state=False))
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("", [])


def test_an_unmatched_profile_name_declines_to_launch(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, DeclineBrowser(profile_name="Nobody Here"))
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("", [])


def test_an_empty_faves_list_opens_no_tabs_but_keeps_the_profile(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, DeclineBrowser(urls=()))
    assert build_manifest(load_config(cfg_path), _PROVIDERS) == ("Profile 2", [])


def test_write_manifest_writes_profile_then_urls(tmp_path: Path):
    output_path = tmp_path / "manifest.txt"
    write_manifest(output_path, "Profile 2", ["https://example.com/1", "https://example.com/2"])
    assert output_path.read_text(encoding="utf-8") == (
        "Profile 2\n"
        "https://example.com/1\n"
        "https://example.com/2\n"
    )


def _regen_cfg(cfg_factory, tmp_path: Path) -> Path:
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Jane Doe"}}}}),
        encoding="utf-8",
    )
    return cfg_factory(
        {
            "random_faves_browser": {
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


_UPSCALED = Path("kino") / "2D" / "AI" / "portrait"


def _ai_video(tmp_path: Path, name: str, *, source: str = "provider") -> Path:
    video = tmp_path / "videos" / "videos" / _UPSCALED / source / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    return video


def _starred(name: str, **columns) -> dict:
    return a_row(f"gen {name}", name, starred=1, **columns)


def _manifest(cfg_factory, tmp_path: Path, gallery: Gallery, providers=_PROVIDERS):
    return build_manifest(load_config(_regen_cfg(cfg_factory, tmp_path)), providers,
                          gallery=gallery)


def test_build_manifest_opens_no_tab_for_a_favorite_no_provider_made(cfg_factory, tmp_path: Path):
    _ai_video(tmp_path, "clip_00001__topaz.mp4", source="homemade")
    gallery = Gallery([_starred("clip_00001_.mp4", params={"positive_prompt": "P"})])

    _, targets = _manifest(cfg_factory, tmp_path, gallery)

    assert targets == []


def test_build_manifest_opens_another_sites_favorite_at_its_gallery(cfg_factory, tmp_path: Path):
    _ai_video(tmp_path, "abc_00001__topaz.mp4", source="provider2")
    gallery = Gallery([_starred("abc_00001_.mp4", params={"positive_prompt": "P"},
                                web_url="https://example.net/image/abc")])
    providers = (*_PROVIDERS, WebProvider(marker="provider2", gallery_url="https://example.net/image/{id}"))

    _, targets = _manifest(cfg_factory, tmp_path, gallery, providers)

    assert [target.url for target in targets] == ["https://example.net/image/abc"]


def test_build_manifest_opens_a_favorite_with_no_recorded_prompt_at_its_gallery(cfg_factory, tmp_path: Path):
    _ai_video(tmp_path, "abc_00001__topaz.mp4")
    gallery = Gallery([_starred("abc_00001_.mp4", web_url="https://example.com/image/abc")])

    _, targets = _manifest(cfg_factory, tmp_path, gallery)

    assert [target.url for target in targets] == ["https://example.com/image/abc"]


def test_build_manifest_targets_the_regenerate_page_when_metadata_exists(cfg_factory, tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")
    gallery = Gallery([_starred("abc_00001_.mp4", params={"positive_prompt": "A PROMPT"},
                                web_url="https://example.com/image/abc")])

    _, targets = _manifest(cfg_factory, tmp_path, gallery)

    assert len(targets) == 1
    assert targets[0].url.startswith("https://example.com/video#ft=")
    assert targets[0].label == "https://example.com/image/abc"
    assert targets[0].video_path == str(video)


def test_an_ai_video_the_gallery_has_not_starred_is_no_favorite(cfg_factory, tmp_path: Path):
    _ai_video(tmp_path, "abc_00001__topaz.mp4")
    gallery = Gallery([a_row("gen-1", "abc_00001_.mp4", starred=0, params={"positive_prompt": "P"})])

    assert _manifest(cfg_factory, tmp_path, gallery)[1] == []


def test_an_ai_videos_own_record_says_nothing_of_whether_it_is_a_favorite(cfg_factory, tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")
    mark_favorite(video, _records(tmp_path), True, page="https://example.com/image/abc")

    assert _manifest(cfg_factory, tmp_path, Gallery([]))[1] == []


def test_a_lock_opens_the_tab_the_startup_browser_opens_for_that_favorite(
        cfg_factory, tmp_path: Path):
    video = _ai_video(tmp_path, "abc_00001__topaz.mp4")
    origenerator = made_up_origenerator(tmp_path, _starred(
        "abc_00001_.mp4", params={"positive_prompt": "A PROMPT"}, web_url="https://example.com/image/abc"))
    project = load_config(_regen_cfg(cfg_factory, tmp_path))
    _, [at_startup] = build_manifest(project, _PROVIDERS,
                                     gallery=Gallery.read(gallery_database_in(origenerator)))
    session = make_config(
        tmp_path / "state",
        regen_media_root=project.regen.media_root,
        regen_metadata_root=project.regen.metadata_root,
        regen_generate_video_url=project.regen.generate_video_url,
        regen_generate_image_url=project.regen.generate_image_url,
        librarys_origenerator=origenerator)

    with patch("fun_time.lock.load_web_providers", return_value=_PROVIDERS), \
         patch("fun_time.lock.star_in_the_gallery"):
        _state, [opened] = a_lock_holds(str(video), BridgeState(), session)

    at_the_lock = Path(url2pathname(urlparse(opened.key).path)).read_text(encoding="utf-8")
    assert at_the_lock == render_tab_page(at_startup, loopback_port=session.loopback_port)


def test_build_manifest_plays_the_original_not_the_upscale(cfg_factory, tmp_path: Path):
    _ai_video(tmp_path, "abc_topaz.mp4")
    original = tmp_path / "videos" / "videos" / "2D" / "AI" / "1_sorted" / "provider" / "portrait" / "abc.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"")
    gallery = Gallery([_starred("abc.mp4", params={"positive_prompt": "P"},
                                web_url="https://example.com/image/abc")])

    _, targets = _manifest(cfg_factory, tmp_path, gallery)

    assert targets[0].video_path == str(original)


def test_build_manifest_falls_back_to_the_gallery_link_without_metadata(cfg_factory, tmp_path: Path):
    video = _favorite_video(tmp_path, "media/provider2/abc.mp4", page="https://example.net/image/abc")

    _, targets = _manifest(cfg_factory, tmp_path, Gallery([]))

    assert targets == [
        TabTarget(
            url="https://example.net/image/abc",
            label="https://example.net/image/abc",
            video_path=str(video),
        )
    ]


def test_a_starred_ai_video_with_no_page_in_the_gallery_is_labeled_with_its_sites_page(
        cfg_factory, tmp_path: Path):
    _ai_video(tmp_path, "def_00001__topaz.mp4")
    gallery = Gallery([_starred("def_00001_.mp4", params={"positive_prompt": "P"})])

    _, targets = _manifest(cfg_factory, tmp_path, gallery)

    assert targets[0].label == "https://example.com/image/def_00001_"


def test_build_manifest_drops_faves_with_nowhere_to_go(cfg_factory, tmp_path: Path):
    _favorite_video(tmp_path, "media/other/abc.mp4")

    _, targets = _manifest(cfg_factory, tmp_path, Gallery([]))

    assert targets == []


def test_the_favorites_are_the_videos_their_records_mark_with_the_pages_they_came_from(tmp_path: Path):
    video = _favorite_video(tmp_path, "other/scene one.mp4", page="https://example.com/1")

    assert favorite_entries(_records(tmp_path), Gallery([]), _PROVIDERS) == [
        FavEntry(local_path=str(video), web_url="https://example.com/1")]


def test_with_no_records_folder_and_no_starred_ai_video_there_are_no_favorites_to_open(
        genaus_folder):
    assert favorite_entries(None, Gallery([]), _PROVIDERS) == []
