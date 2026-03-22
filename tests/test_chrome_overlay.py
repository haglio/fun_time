from __future__ import annotations

import json
import random
from pathlib import Path

from fun_time.chrome_overlay import (
    build_manifest,
    choose_random_urls,
    load_folder_urls,
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


def test_load_folder_urls_reads_named_folder(tmp_path: Path):
    bookmarks_path = tmp_path / "Bookmarks"
    bookmarks_path.write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "children": [
                            {
                                "name": "Fun Time Favs",
                                "type": "folder",
                                "children": [
                                    {"type": "url", "url": "https://example.com/a"},
                                    {"type": "url", "url": "https://example.com/b"},
                                ],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_folder_urls(bookmarks_path, "Fun Time Favs") == [
        "https://example.com/a",
        "https://example.com/b",
    ]


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
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "children": [
                            {
                                "name": "Fun Time Favs",
                                "type": "folder",
                                "children": [
                                    {"type": "url", "url": "https://example.com/1"},
                                    {"type": "url", "url": "https://example.com/2"},
                                ],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg_path = cfg_factory(
        {
            "chrome_overlay": {
                "enabled": True,
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
            }
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
