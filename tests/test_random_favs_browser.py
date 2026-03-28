from __future__ import annotations

import json
import random
from pathlib import Path

from fun_time.random_favs_browser import (
    build_manifest,
    choose_random_urls,
    extract_url_from_hyperlink,
    load_favs_web_urls,
    resolve_profile_directory,
    write_manifest,
)


def test_resolve_profile_directory_finds_named_profile(tmp_path: Path):
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Alex"},
                        "Profile 2": {"name": "Blair"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert resolve_profile_directory(user_data_dir, "Blair") == "Profile 2"



def test_choose_random_urls_uses_requested_count():
    rng = random.Random(123)
    urls = [f"https://example.com/{i}" for i in range(20)]
    chosen = choose_random_urls(urls, 10, rng)
    assert len(chosen) == 10
    assert len(set(chosen)) == 10


def test_build_manifest_returns_profile_and_urls(cfg_factory, tmp_path: Path):
    user_data_dir = tmp_path / "User Data"
    profile_dir = user_data_dir / "Profile 2"
    profile_dir.mkdir(parents=True)
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Blair"}}}}),
        encoding="utf-8",
    )
    (profile_dir / "Bookmarks").write_text(
        json.dumps({"roots": {"bookmark_bar": {"children": []}}}),
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
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )

    profile_name, urls = build_manifest(cfg_path)
    assert profile_name == "Profile 2"
    assert sorted(urls) == ["https://example.com/1", "https://example.com/2"]


def test_write_manifest_writes_profile_then_urls(tmp_path: Path):
    output_path = tmp_path / "manifest.txt"
    write_manifest(output_path, "Profile 2", ["https://example.com/1", "https://example.com/2"])
    assert output_path.read_text(encoding="utf-8") == (
        "Profile 2\n"
        "https://example.com/1\n"
        "https://example.com/2\n"
    )


# --- favs.csv web URL loading ---


def test_extract_url_from_hyperlink_formula():
    cell = '=HYPERLINK("https://example.net/image/abc";"https://example.net/image/abc")'
    assert extract_url_from_hyperlink(cell) == "https://example.net/image/abc"


def test_extract_url_from_hyperlink_plain_url():
    assert extract_url_from_hyperlink("https://example.com/1") == "https://example.com/1"


def test_extract_url_from_hyperlink_empty():
    assert extract_url_from_hyperlink("") == ""


def test_load_favs_web_urls_reads_web_url_column(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    favs.write_text(
        'local_file,web_url\r\n'
        '"=HYPERLINK(""file:///C:/img/a.png"";""C:\\img\\a.png"")",'
        '"=HYPERLINK(""https://example.net/image/a"";""https://example.net/image/a"")"\r\n'
        '"=HYPERLINK(""file:///C:/img/b.png"";""C:\\img\\b.png"")",'
        '"=HYPERLINK(""https://example.com/image/b"";""https://example.com/image/b"")"\r\n',
        encoding="utf-8",
    )

    urls = load_favs_web_urls(favs)
    assert urls == [
        "https://example.net/image/a",
        "https://example.com/image/b",
    ]


def test_load_favs_web_urls_skips_rows_with_empty_web_url(tmp_path: Path):
    favs = tmp_path / "favs.csv"
    favs.write_text(
        'local_file,web_url\r\n'
        '"=HYPERLINK(""file:///C:/img/a.png"";""C:\\img\\a.png"")",""\r\n'
        '"=HYPERLINK(""file:///C:/img/b.png"";""C:\\img\\b.png"")",'
        '"=HYPERLINK(""https://example.net/image/b"";""https://example.net/image/b"")"\r\n',
        encoding="utf-8",
    )

    urls = load_favs_web_urls(favs)
    assert urls == ["https://example.net/image/b"]


def test_load_favs_web_urls_missing_file(tmp_path: Path):
    assert load_favs_web_urls(tmp_path / "missing.csv") == []


def test_build_manifest_uses_favs_csv_web_urls(cfg_factory, tmp_path: Path):
    """build_manifest should source URLs from favs.csv, not Chrome bookmarks."""
    user_data_dir = tmp_path / "User Data"
    profile_dir = user_data_dir / "Profile 2"
    profile_dir.mkdir(parents=True)
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Blair"}}}}),
        encoding="utf-8",
    )
    # Chrome bookmarks contain file:// URIs — these should NOT be used
    (profile_dir / "Bookmarks").write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "children": [
                            {
                                "name": "Fun Time Favs",
                                "type": "folder",
                                "children": [
                                    {"type": "url", "url": "file:///C:/img/a.png"},
                                    {"type": "url", "url": "file:///C:/img/b.png"},
                                ],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # favs.csv has the correct web URLs
    favs = tmp_path / "favs.csv"
    favs.write_text(
        'local_file,web_url\r\n'
        '"=HYPERLINK(""file:///C:/img/a.png"";""C:\\img\\a.png"")",'
        '"=HYPERLINK(""https://example.net/image/a"";""https://example.net/image/a"")"\r\n'
        '"=HYPERLINK(""file:///C:/img/b.png"";""C:\\img\\b.png"")",'
        '"=HYPERLINK(""https://example.com/image/b"";""https://example.com/image/b"")"\r\n',
        encoding="utf-8",
    )

    cfg_path = cfg_factory(
        {
            "paths": {"favs_file": str(favs)},
            "random_favs_browser": {
                "enabled": True,
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            },
        }
    )

    profile_name, urls = build_manifest(cfg_path)
    assert profile_name == "Profile 2"
    # Must be web URLs from favs.csv, NOT file:// URIs from bookmarks
    assert sorted(urls) == [
        "https://example.com/image/b",
        "https://example.net/image/a",
    ]
