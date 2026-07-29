"""The main library browser — folder tiles you walk, then the videos inside."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent

from fun_time.library_browser import (
    ICON_HEIGHT,
    ICON_WIDTH,
    LibraryBrowserWindow,
    browse_library,
    load_browser_config,
    pick_file_for,
    rows_needing_stills,
)
from fun_time.library_handles import LibraryHandle
from fun_time.library_tree import SubFolder
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.thumbnail_cache import thumbnail_path
from fun_time import load_config


def _handle(title: str, *versions: str, section: str = "main") -> LibraryHandle:
    return LibraryHandle(
        title=title, versions=versions or (f"C:/videos/{title}.mp4",), section=section,
    )


def _labels(window: LibraryBrowserWindow) -> list[str]:
    return [window.item(row).text() for row in range(window.count())]


def _backspace() -> QKeyEvent:
    return QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Backspace.value, Qt.KeyboardModifier.NoModifier,
    )


def test_a_browse_opens_on_the_librarys_own_folders(tmp_path: Path):
    """Not on a wall of videos: the top level is one tile per folder, counted."""
    window = LibraryBrowserWindow(
        [
            _handle("Beta Scene", section="big_batch/whole"),
            _handle("Excerpt 1", section="big_batch/cuts"),
            _handle("alpha scene", section="small_batch"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _video: None,
    )

    assert _labels(window) == ["big_batch  (2)", "small_batch  (1)"]
    assert all(isinstance(what, SubFolder) for what in window.rows)


def test_opening_a_folder_shows_what_is_in_it_and_a_way_back(tmp_path: Path):
    handles = [
        _handle("Beta Scene", section="big_batch/whole"),
        _handle("Excerpt 1", section="big_batch/cuts"),
        _handle("Excerpt 2", section="big_batch/cuts"),
    ]
    window = LibraryBrowserWindow(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)

    window.itemActivated.emit(window.item(0))

    assert _labels(window) == ["all folders", "whole  (1)", "cuts  (2)"]
    assert window.rows[0] is None


def test_the_deepest_folder_lays_out_every_video_under_it(tmp_path: Path):
    """No processing folder is ever a step — the videos come up in one grid."""
    handles = [_handle(f"Excerpt {i}", section="big_batch/cuts") for i in range(3)]
    window = LibraryBrowserWindow(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)

    window.open_folder(("big_batch", "cuts"))

    assert _labels(window) == ["back", "Excerpt 0", "Excerpt 1", "Excerpt 2"]
    assert window.rows[1:] == handles


def test_the_way_back_goes_back(tmp_path: Path):
    handles = [_handle("Excerpt 1", section="big_batch/cuts")]
    window = LibraryBrowserWindow(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("big_batch", "cuts"))

    window.itemActivated.emit(window.item(0))

    assert _labels(window) == ["all folders", "cuts  (1)"]


def test_backspace_goes_back_too(tmp_path: Path):
    """The key every other file browser uses for it, and the grid has the focus."""
    handles = [_handle("Excerpt 1", section="big_batch/cuts")]
    window = LibraryBrowserWindow(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("big_batch", "cuts"))

    window.keyPressEvent(_backspace())

    assert _labels(window) == ["all folders", "cuts  (1)"]


def test_backspace_at_the_top_stays_put(tmp_path: Path):
    window = LibraryBrowserWindow(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.keyPressEvent(_backspace())

    assert _labels(window) == ["big_batch  (1)"]


def test_the_selection_starts_on_the_first_thing_worth_opening(tmp_path: Path):
    """Never on the way back — arrowing off the top of a folder is not the point."""
    window = LibraryBrowserWindow(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("big_batch",))

    assert window.currentRow() == 1


def test_activating_a_video_reports_that_handles_playable_version(tmp_path: Path):
    picked: list[str] = []
    handles = [
        _handle("alpha scene", "C:/videos/0 unsorted/alpha.mp4", section="main"),
        _handle(
            "Beta Scene",
            "C:/videos/3_good_to_go/beta_big.mp4",
            "C:/videos/0 unsorted/beta.mp4",
            section="main",
        ),
    ]
    window = LibraryBrowserWindow(handles, thumbnail_cache=tmp_path, on_pick=picked.append)
    window.open_folder(("main",))

    window.itemActivated.emit(window.item(window.rows.index(handles[1])))

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
    window.open_folder(("main",))

    pictured_row, bare_row = (window.rows.index(handle) for handle in handles)
    assert not window.item(pictured_row).icon().isNull()
    assert window.item(bare_row).icon().isNull()
    assert rows_needing_stills(window.rows, cache) == [bare_row]


def test_a_folder_tile_is_pictured_with_a_still_from_inside_it(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    video = tmp_path / "alpha.mp4"
    video.write_bytes(b"\0")
    Image.new("RGB", (16, 9)).save(thumbnail_path(video, cache), "JPEG")

    window = LibraryBrowserWindow(
        [_handle("alpha scene", str(video), section="big_batch")],
        thumbnail_cache=cache,
        on_pick=lambda _video: None,
    )

    assert not window.item(0).icon().isNull()


def test_the_browser_keeps_out_of_the_taskbar(tmp_path: Path):
    """A browse is a thing you open and dismiss, not a program that is running.

    Qt's Tool window type is what clears the taskbar button on Windows.  Without
    it the grid gets its own indicator — and, having declared no identity of its
    own, one Windows hangs off whatever unrelated app it can pair it with.
    """
    window = LibraryBrowserWindow(
        [_handle("alpha scene")], thumbnail_cache=tmp_path, on_pick=lambda _video: None,
    )

    assert window.windowFlags() & Qt.WindowType.Tool


def test_the_title_says_which_folder_is_open(tmp_path: Path):
    window = LibraryBrowserWindow(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("big_batch", "cuts"))

    assert window.windowTitle().endswith("big_batch/cuts")


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


def test_closing_the_browse_tells_the_process_it_is_over(tmp_path: Path):
    """Nothing else can: Qt does not count a Tool window towards the last-window
    quit, so without this the picked video would sit in the result file with the
    bridge still blocked on a process that had nothing left to do."""
    ended: list[str] = []
    window = LibraryBrowserWindow(
        [_handle("alpha scene")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _video: None,
        on_close=lambda: ended.append("over"),
    )
    window.show()

    window.close()

    assert ended == ["over"]


def test_picking_a_video_ends_the_browse_as_well_as_reporting_it(tmp_path: Path):
    picked: list[str] = []
    ended: list[str] = []
    handles = [_handle("alpha scene", "C:/videos/alpha.mp4", section="main")]
    window = LibraryBrowserWindow(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=picked.append,
        on_close=lambda: ended.append("over"),
    )
    window.open_folder(("main",))
    window.show()

    window.itemActivated.emit(window.item(window.rows.index(handles[0])))

    assert picked == ["C:/videos/alpha.mp4"]
    assert ended == ["over"]


def test_a_still_is_scaled_to_fit_its_tile_and_never_stretched(tmp_path: Path):
    """Grown until it meets an edge, with its proportions untouched.

    A tall video and a wide one are the same tile, so one of the two axes always
    has room to spare; filling both would squash whichever picture does not share
    the tile's shape.  The cached stills are capped well below the tile, so this
    scales *up* as well as down — a still left at its own size sits in a corner
    of the space it was given.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    tall = tmp_path / "tall.mp4"
    tall.write_bytes(b"\0")
    Image.new("RGB", (60, 160)).save(thumbnail_path(tall, cache), "JPEG")

    window = LibraryBrowserWindow(
        [_handle("tall scene", str(tall))], thumbnail_cache=cache, on_pick=lambda _v: None,
    )
    window.open_folder(("main",))
    drawn = window.item(window.rows.index(window.rows[-1])).icon().pixmap(ICON_WIDTH, ICON_HEIGHT)

    assert drawn.height() == ICON_HEIGHT, "the long edge should meet the tile's edge"
    assert drawn.width() == round(ICON_HEIGHT * 60 / 160), "proportions must not change"
    assert drawn.width() < ICON_WIDTH, "the short edge stops before the other edge"


def test_a_folder_tile_shows_four_stills_in_a_grid(tmp_path: Path):
    """Four videos from inside, laid out two by two, each keeping its shape."""
    cache = tmp_path / "cache"
    cache.mkdir()
    handles = []
    for index in range(4):
        video = tmp_path / f"v{index}.mp4"
        video.write_bytes(b"\0" * (10 + index))
        Image.new("RGB", (160, 90), (20 * index, 90, 140)).save(
            thumbnail_path(video, cache), "JPEG"
        )
        handles.append(_handle(f"Scene {index}", str(video), section="big_batch"))

    window = LibraryBrowserWindow(handles, thumbnail_cache=cache, on_pick=lambda _v: None)
    drawn = window.item(0).icon().pixmap(ICON_WIDTH, ICON_HEIGHT)

    assert not drawn.isNull()
    assert (drawn.width(), drawn.height()) == (ICON_WIDTH, ICON_HEIGHT)
    # Four distinct fills, one per cell, so nothing was drawn over or left out.
    image = drawn.toImage()
    corners = {
        image.pixel(ICON_WIDTH // 4, ICON_HEIGHT // 4),
        image.pixel(3 * ICON_WIDTH // 4, ICON_HEIGHT // 4),
        image.pixel(ICON_WIDTH // 4, 3 * ICON_HEIGHT // 4),
        image.pixel(3 * ICON_WIDTH // 4, 3 * ICON_HEIGHT // 4),
    }
    assert len(corners) == 4


def test_a_folder_holding_one_video_gives_it_the_whole_tile(tmp_path: Path):
    """No point quartering a tile around a single still."""
    cache = tmp_path / "cache"
    cache.mkdir()
    video = tmp_path / "only.mp4"
    video.write_bytes(b"\0")
    Image.new("RGB", (160, 90), (200, 40, 90)).save(thumbnail_path(video, cache), "JPEG")

    window = LibraryBrowserWindow(
        [_handle("only scene", str(video), section="big_batch")],
        thumbnail_cache=cache,
        on_pick=lambda _v: None,
    )
    drawn = window.item(0).icon().pixmap(ICON_WIDTH, ICON_HEIGHT)

    assert drawn.width() == ICON_WIDTH, "one still fills the tile as a video's would"
