from __future__ import annotations

import json
import random
from pathlib import Path

from fun_time.config import load_config
from fun_time.random_favs_browser import (
    FavEntry,
    build_manifest,
    choose_random,
    extract_path_from_hyperlink,
    extract_url_from_hyperlink,
    load_favs_entries,
    resolve_profile_directory,
    write_manifest,
)
from fun_time.rfb_tab_page import TabTarget


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


def test_build_manifest_returns_profile_and_targets(cfg_factory, tmp_path: Path):
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Jane Doe"}}}}),
        encoding="utf-8",
    )
    favs = tmp_path / "favs.csv"
    favs.write_text(
        'local_file,web_url\r\n'
        '"","https://example.com/1"\r\n'
        '"","https://example.com/2"\r\n',
        encoding="utf-8",
    )
    cfg_path = cfg_factory(
        {
            "paths": {"favs_file": str(favs)},
            "random_favs_browser": {
                "enabled": True,
                "profile_name": "Jane Doe",
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )

    profile_name, targets = build_manifest(load_config(cfg_path))
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
    favs = tmp_path / "favs.csv"
    rows = "".join(f'"","{url}"\r\n' for url in urls)
    favs.write_text("local_file,web_url\r\n" + rows, encoding="utf-8")
    return cfg_factory(
        {
            "paths": {"favs_file": str(favs)},
            "random_favs_browser": {
                "enabled": enabled,
                "profile_name": "Jane Doe",
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )


def test_a_disabled_browser_declines_to_launch(cfg_factory, tmp_path: Path):
    """random_favs_browser.enabled is a documented public config key (clipper
    reads it too), and turning it off must mean no profile and no tabs."""
    cfg_path = _decline_config(cfg_factory, tmp_path, enabled=False)
    assert build_manifest(load_config(cfg_path)) == ("", [])


def test_a_missing_local_state_declines_to_launch(cfg_factory, tmp_path: Path):
    """No Chrome Local State file — a machine without that Chrome profile
    store — reads as 'no profile', not as a crash."""
    cfg_path = _decline_config(cfg_factory, tmp_path, local_state=False)
    assert build_manifest(load_config(cfg_path)) == ("", [])


def test_an_unmatched_profile_name_declines_to_launch(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, profile_name="Nobody Here")
    assert build_manifest(load_config(cfg_path)) == ("", [])


def test_an_empty_favs_list_opens_no_tabs_but_keeps_the_profile(cfg_factory, tmp_path: Path):
    cfg_path = _decline_config(cfg_factory, tmp_path, urls=())
    assert build_manifest(load_config(cfg_path)) == ("Profile 2", [])


def test_write_manifest_writes_profile_then_urls(tmp_path: Path):
    output_path = tmp_path / "manifest.txt"
    write_manifest(output_path, "Profile 2", ["https://example.com/1", "https://example.com/2"])
    assert output_path.read_text(encoding="utf-8") == (
        "Profile 2\n"
        "https://example.com/1\n"
        "https://example.com/2\n"
    )


# --- regenerate targets ---


def _fav_row(local_path: str, web_url: str) -> str:
    local = f'=HYPERLINK(""file:///x"";""{local_path}"")'
    web = f'=HYPERLINK(""{web_url}"";""{web_url}"")' if web_url else ""
    return f'"{local}","{web}"\r\n'


def _regen_cfg(cfg_factory, tmp_path: Path, favs_rows: str) -> Path:
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Jane Doe"}}}}),
        encoding="utf-8",
    )
    favs = tmp_path / "favs.csv"
    favs.write_text("local_file,web_url\r\n" + favs_rows, encoding="utf-8")
    return cfg_factory(
        {
            "paths": {"favs_file": str(favs)},
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
_UPSCALED = Path("2_outbox") / "upscaled_by_orientation" / "portrait" / "provider"


def _write_sidecar(tmp_path: Path, name: str, metadata: dict) -> Path:
    video = tmp_path / "videos" / "videos" / "2D" / "AI" / _UPSCALED / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    sidecar = tmp_path / "videos" / "metadata" / "2D" / "AI" / _UPSCALED / Path(name).with_suffix(".json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    return video


def test_build_manifest_targets_the_regenerate_page_when_metadata_exists(cfg_factory, tmp_path: Path):
    """A dead gallery link becomes the prefilled Provider generate page."""
    video = _write_sidecar(tmp_path, "abc_topaz.mp4", {"video": {"prompt": "A PROMPT"}})
    cfg_path = _regen_cfg(cfg_factory, tmp_path, _fav_row(str(video), "https://example.com/image/abc"))

    _, targets = build_manifest(load_config(cfg_path))

    assert len(targets) == 1
    assert targets[0].url.startswith("https://example.com/video#ft=")
    assert targets[0].label == "https://example.com/image/abc"
    # The tab plays the clip you are deciding whether to recreate.
    assert targets[0].video_path == str(video)


def test_build_manifest_plays_the_original_not_the_upscale(cfg_factory, tmp_path: Path):
    """The upscale is hundreds of MB of HEVC; its original is a few MB of H.264."""
    upscaled = _write_sidecar(tmp_path, "abc_topaz.mp4", {"video": {"prompt": "P"}})
    original = tmp_path / "videos" / "videos" / "2D" / "AI" / "1_sorted" / "provider" / "portrait" / "abc.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"")
    cfg_path = _regen_cfg(cfg_factory, tmp_path, _fav_row(str(upscaled), "https://example.com/image/abc"))

    _, targets = build_manifest(load_config(cfg_path))

    assert targets[0].video_path == str(original)


def test_build_manifest_falls_back_to_the_gallery_link_without_metadata(cfg_factory, tmp_path: Path):
    """No sidecar (e.g. a provider2 fav) leaves the stored link untouched."""
    cfg_path = _regen_cfg(
        cfg_factory,
        tmp_path,
        _fav_row("C:\\media\\provider2\\abc.mp4", "https://example.net/image/abc"),
    )

    _, targets = build_manifest(load_config(cfg_path))

    assert targets == [
        TabTarget(
            url="https://example.net/image/abc",
            label="https://example.net/image/abc",
            video_path="C:\\media\\provider2\\abc.mp4",
        )
    ]


def test_build_manifest_labels_a_gallery_less_fav_with_its_filename(cfg_factory, tmp_path: Path):
    video = _write_sidecar(tmp_path, "def_topaz.mp4", {"video": {"prompt": "P"}})
    cfg_path = _regen_cfg(cfg_factory, tmp_path, _fav_row(str(video), ""))

    _, targets = build_manifest(load_config(cfg_path))

    assert targets[0].label == "def_topaz.mp4"


def test_build_manifest_drops_favs_with_nowhere_to_go(cfg_factory, tmp_path: Path):
    """A non-Provider local file with no gallery link yields no tab at all."""
    cfg_path = _regen_cfg(cfg_factory, tmp_path, _fav_row("C:\\media\\other\\abc.mp4", ""))

    _, targets = build_manifest(load_config(cfg_path))

    assert targets == []


# --- favs.csv web URL loading ---


def test_extract_url_from_hyperlink_formula():
    cell = '=HYPERLINK("https://example.net/image/abc";"https://example.net/image/abc")'
    assert extract_url_from_hyperlink(cell) == "https://example.net/image/abc"


def test_extract_url_from_hyperlink_plain_url():
    assert extract_url_from_hyperlink("https://example.com/1") == "https://example.com/1"


def test_extract_url_from_hyperlink_empty():
    assert extract_url_from_hyperlink("") == ""


def test_extract_path_from_hyperlink_returns_display_text():
    cell = '=HYPERLINK("file:///C:/img/a%20b.mp4";"C:\\img\\a b.mp4")'
    assert extract_path_from_hyperlink(cell) == "C:\\img\\a b.mp4"


def test_extract_path_from_hyperlink_plain_value():
    assert extract_path_from_hyperlink("C:\\img\\a.mp4") == "C:\\img\\a.mp4"


def test_extract_path_from_hyperlink_malformed_formula():
    assert extract_path_from_hyperlink('=HYPERLINK("file:///C:/img/a.mp4"') == ""


def test_load_favs_entries_reads_local_path_and_web_url(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    favs.write_text(
        'local_file,web_url\r\n'
        '"=HYPERLINK(""file:///C:/img/a.mp4"";""C:\\img\\a.mp4"")",'
        '"=HYPERLINK(""https://example.net/image/a"";""https://example.net/image/a"")"\r\n'
        '"=HYPERLINK(""file:///C:/img/b.mp4"";""C:\\img\\b.mp4"")",""\r\n',
        encoding="utf-8",
    )

    assert load_favs_entries(favs) == [
        FavEntry(local_path="C:\\img\\a.mp4", web_url="https://example.net/image/a"),
        FavEntry(local_path="C:\\img\\b.mp4", web_url=""),
    ]


def test_load_favs_entries_skips_rows_with_neither_column(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    favs.write_text(
        'local_file,web_url\r\n'
        '"",""\r\n'
        '"=HYPERLINK(""file:///C:/img/b.mp4"";""C:\\img\\b.mp4"")",""\r\n',
        encoding="utf-8",
    )

    assert load_favs_entries(favs) == [FavEntry(local_path="C:\\img\\b.mp4", web_url="")]


def test_load_favs_entries_missing_file(tmp_path: Path):
    assert load_favs_entries(tmp_path / "missing.csv") == []
