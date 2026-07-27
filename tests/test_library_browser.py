"""The primary library browser — a grid of version-group handles."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from fun_time.library_browser import (
    LibraryBrowserWindow,
    browse_library,
    load_browser_config,
    pick_file_for,
    rows_without_thumbnails,
)
from fun_time.library_handles import LibraryHandle
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.thumbnail_cache import thumbnail_path
from fun_time import load_config


def _handle(title: str, *versions: str) -> LibraryHandle:
    return LibraryHandle(title=title, versions=versions or (f"C:/videos/{title}.mp4",))


def test_the_grid_shows_one_tile_per_handle_in_the_order_given(tmp_path: Path):
    window = LibraryBrowserWindow(
        [_handle("alpha scene"), _handle("Beta Scene"), _handle("Gamma Scene")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _video: None,
    )

    assert [window.item(row).text() for row in range(window.count())] == [
        "alpha scene",
        "Beta Scene",
        "Gamma Scene",
    ]


def test_activating_a_tile_reports_that_handles_playable_version(tmp_path: Path):
    picked: list[str] = []
    window = LibraryBrowserWindow(
        [
            _handle("alpha scene", "C:/videos/0 unsorted/alpha.mp4"),
            _handle("Beta Scene", "C:/videos/3_good_to_go/beta_big.mp4", "C:/videos/0 unsorted/beta.mp4"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=picked.append,
    )

    window.itemActivated.emit(window.item(1))

    assert picked == ["C:/videos/3_good_to_go/beta_big.mp4"]


def test_abandoning_the_browse_reports_nothing(tmp_path: Path):
    """Closing the window without picking says nothing — no pick, no play."""
    picked: list[str] = []
    window = LibraryBrowserWindow(
        [_handle("alpha scene")], thumbnail_cache=tmp_path, on_pick=picked.append,
    )
    window.show()

    window.close()

    assert picked == []
    assert window.isHidden()


def test_a_cached_still_becomes_the_tile_and_only_misses_are_extracted(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    pictured = tmp_path / "alpha.mp4"
    pictured.write_bytes(b"\0")
    bare = tmp_path / "beta.mp4"
    bare.write_bytes(b"\0")
    Image.new("RGB", (16, 9)).save(thumbnail_path(pictured, cache), "JPEG")

    handles = [_handle("alpha scene", str(pictured)), _handle("Beta Scene", str(bare))]
    window = LibraryBrowserWindow(handles, thumbnail_cache=cache, on_pick=lambda _video: None)

    assert not window.item(0).icon().isNull()
    assert window.item(1).icon().isNull()
    assert rows_without_thumbnails(handles, cache) == [1]


def test_the_browser_reads_its_library_from_the_session_manifest(tmp_path: Path, cfg_factory):
    config = load_config(cfg_factory({"regen": {"metadata_root": str(tmp_path / "videos" / "metadata")}}))
    manifest = write_windows_bridge_manifest(config)

    browser_config = load_browser_config(manifest)

    assert browser_config.sources == "|".join(str(path) for path in config.paths.nau_library_dirs)
    assert browser_config.metadata_root == tmp_path / "videos" / "metadata"
    assert browser_config.thumbnail_cache.parent == manifest.parent


def test_browsing_runs_the_browser_and_returns_what_it_picked(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        pick_file_for(manifest).write_text(r"C:\videos\beta.mp4", encoding="utf-8")

    picked = browse_library(manifest, r"C:\python.exe", over=(10, 20, 300, 400), runner=fake_run)

    assert picked == r"C:\videos\beta.mp4"
    assert commands == [[
        r"C:\python.exe", "-m", "fun_time.library_browser",
        str(manifest), str(pick_file_for(manifest)),
        "--x", "10", "--y", "20", "--width", "300", "--height", "400",
    ]]


def test_an_abandoned_browse_picks_nothing(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")

    assert browse_library(manifest, r"C:\python.exe", runner=lambda _c, **_k: None) is None


def test_an_abandoned_browse_never_replays_the_previous_pick(tmp_path: Path):
    """The pick file outlives the browse that wrote it, so it is cleared first.

    Without that, closing the browser without choosing would replay whatever was
    picked last time — the session would jump to a video nobody asked for.
    """
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    pick_file_for(manifest).write_text(r"C:\videos\alpha.mp4", encoding="utf-8")

    assert browse_library(manifest, r"C:\python.exe", runner=lambda _c, **_k: None) is None
