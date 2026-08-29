"""The main library browser — folder tiles you walk, then the videos inside."""
from __future__ import annotations

import pytest

from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QListWidget

from fun_time.library_browser import (
    ICON_HEIGHT,
    ICON_WIDTH,
    NON_LETTER_HEADING,
    SIDEBAR_WIDTH,
    IndexLine,
    LibraryBrowserWindow,
    alphabetical_index,
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


def _labels(view: QListWidget) -> list[str]:
    return [view.item(row).text() for row in range(view.count())]


def _backspace() -> QKeyEvent:
    return QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Backspace.value, Qt.KeyboardModifier.NoModifier,
    )


@pytest.fixture()
def browser():
    """Build LibraryBrowserWindow(s) that always close with the test.

    Each construction is a real top-level Qt window in the shared session
    QApplication; before this factory, thirty were built per run and three
    ever closed, the rest living on for the whole session.
    """
    opened: list[LibraryBrowserWindow] = []

    def build(handles, **kwargs):
        window = LibraryBrowserWindow(handles, **kwargs)
        opened.append(window)
        return window

    yield build
    for window in opened:
        window.close()


def test_a_browse_opens_on_the_librarys_own_folders(browser, tmp_path: Path):
    """Not on a wall of videos: the top level is one tile per folder, counted."""
    window = browser(
        [
            _handle("Beta Scene", section="big_batch/whole"),
            _handle("Excerpt 1", section="big_batch/cuts"),
            _handle("alpha scene", section="small_batch"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _video: None,
    )

    assert _labels(window.grid) == ["big_batch  (2)", "small_batch  (1)"]
    assert all(isinstance(what, SubFolder) for what in window.grid.rows)


def test_opening_a_folder_shows_what_is_in_it_and_a_way_back(browser, tmp_path: Path):
    handles = [
        _handle("Beta Scene", section="big_batch/whole"),
        _handle("Excerpt 1", section="big_batch/cuts"),
        _handle("Excerpt 2", section="big_batch/cuts"),
    ]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)

    window.grid.itemActivated.emit(window.grid.item(0))

    assert _labels(window.grid) == ["all folders", "whole  (1)", "cuts  (2)"]
    assert window.grid.rows[0] is None


def test_the_deepest_folder_lays_out_every_video_under_it(browser, tmp_path: Path):
    """No processing folder is ever a step — the videos come up in one grid."""
    handles = [_handle(f"Excerpt {i}", section="big_batch/cuts") for i in range(3)]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)

    window.open_folder(("big_batch", "cuts"))

    assert _labels(window.grid) == ["back", "Excerpt 0", "Excerpt 1", "Excerpt 2"]
    assert window.grid.rows[1:] == handles


def test_the_way_back_goes_back(browser, tmp_path: Path):
    handles = [_handle("Excerpt 1", section="big_batch/cuts")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("big_batch", "cuts"))

    window.grid.itemActivated.emit(window.grid.item(0))

    assert _labels(window.grid) == ["all folders", "cuts  (1)"]


def test_backspace_goes_back_too(browser, tmp_path: Path):
    """The key every other file browser uses for it, and the grid has the focus."""
    handles = [_handle("Excerpt 1", section="big_batch/cuts")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("big_batch", "cuts"))

    window.grid.keyPressEvent(_backspace())

    assert _labels(window.grid) == ["all folders", "cuts  (1)"]


def test_backspace_at_the_top_stays_put(browser, tmp_path: Path):
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.grid.keyPressEvent(_backspace())

    assert _labels(window.grid) == ["big_batch  (1)"]


def test_the_selection_starts_on_the_first_thing_worth_opening(browser, tmp_path: Path):
    """Never on the way back — arrowing off the top of a folder is not the point."""
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("big_batch",))

    assert window.grid.currentRow() == 1


def test_activating_a_video_reports_that_handles_playable_version(browser, tmp_path: Path):
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
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=picked.append)
    window.open_folder(("main",))

    window.grid.itemActivated.emit(window.grid.item(window.grid.rows.index(handles[1])))

    assert picked == ["C:/videos/3_good_to_go/beta_big.mp4"]


def test_abandoning_the_browse_reports_nothing(browser, tmp_path: Path):
    """Closing the window without picking says nothing — no pick, no play."""
    picked: list[str] = []
    window = browser(
        [_handle("alpha scene")], thumbnail_cache=tmp_path, on_pick=picked.append,
    )
    window.show()

    window.close()

    assert picked == []
    assert window.isHidden()


def test_a_cached_still_becomes_the_tile_and_only_misses_are_extracted(browser, tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    pictured = tmp_path / "alpha.mp4"
    pictured.write_bytes(b"\0")
    bare = tmp_path / "beta.mp4"
    bare.write_bytes(b"\0")
    Image.new("RGB", (16, 9)).save(thumbnail_path(pictured, cache), "JPEG")

    handles = [_handle("alpha scene", str(pictured)), _handle("Beta Scene", str(bare))]
    window = browser(handles, thumbnail_cache=cache, on_pick=lambda _video: None)
    window.open_folder(("main",))

    pictured_row, bare_row = (window.grid.rows.index(handle) for handle in handles)
    assert not window.grid.item(pictured_row).icon().isNull()
    assert window.grid.item(bare_row).icon().isNull()
    assert rows_needing_stills(window.grid.rows, cache) == [bare_row]


def test_a_folder_tile_is_pictured_with_a_still_from_inside_it(browser, tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    video = tmp_path / "alpha.mp4"
    video.write_bytes(b"\0")
    Image.new("RGB", (16, 9)).save(thumbnail_path(video, cache), "JPEG")

    window = browser(
        [_handle("alpha scene", str(video), section="big_batch")],
        thumbnail_cache=cache,
        on_pick=lambda _video: None,
    )

    assert not window.grid.item(0).icon().isNull()


def test_the_browser_keeps_out_of_the_taskbar(browser, tmp_path: Path):
    """A browse is a thing you open and dismiss, not a program that is running.

    Qt's Tool window type is what clears the taskbar button on Windows.  Without
    it the grid gets its own indicator — and, having declared no identity of its
    own, one Windows hangs off whatever unrelated app it can pair it with.
    """
    window = browser(
        [_handle("alpha scene")], thumbnail_cache=tmp_path, on_pick=lambda _video: None,
    )

    assert window.windowFlags() & Qt.WindowType.Tool


def test_the_title_says_which_folder_is_open(browser, tmp_path: Path):
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("big_batch", "cuts"))

    assert window.windowTitle().endswith("big_batch/cuts")


# --- the alphabetical sidebar -------------------------------------------------


def test_the_sidebar_lists_the_folders_videos_under_a_heading_per_letter(browser, tmp_path: Path):
    window = browser(
        [
            _handle("Alpha Scene", section="main"),
            _handle("Beta Scene", section="main"),
            _handle("Another Scene", section="main"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("main",))

    assert _labels(window.index) == ["A", "Alpha Scene", "Another Scene", "B", "Beta Scene"]


def test_the_sidebar_is_alphabetical_where_the_grid_is_ranked(browser, tmp_path: Path):
    """The whole reason for two views: the grid's order is not a way to find a title.

    Folder tiles come up in the library's own ranking — biggest source folder
    first — so an alphabetical walk across the grid is a walk across a wrapped
    grid of stills.  The sidebar puts the same folder up sorted by name instead.
    """
    window = browser(
        [
            _handle("Zulu Scene", section="big_batch"),
            _handle("Alpha Scene", section="big_batch"),
            _handle("Mike Scene", section="small_batch"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    assert _labels(window.grid) == ["big_batch  (2)", "small_batch  (1)"]
    assert _labels(window.index) == ["B", "big_batch", "S", "small_batch"]


def test_a_heading_is_not_something_the_arrows_can_land_on(browser, tmp_path: Path):
    """It names a group rather than being one of them, so Qt steps over it.

    Flagless is what does that: arrow navigation and type-ahead both skip a
    disabled row, which keeps a walk down the index a walk down its names.
    """
    window = browser(
        [_handle("Alpha Scene", section="main")], thumbnail_cache=tmp_path, on_pick=lambda _v: None,
    )
    window.open_folder(("main",))

    heading, name = window.index.item(0), window.index.item(1)

    assert heading.flags() == Qt.ItemFlag.NoItemFlags
    assert name.flags() & Qt.ItemFlag.ItemIsSelectable


def test_a_name_with_no_letter_to_file_under_goes_to_the_hash_heading(browser, tmp_path: Path):
    """A heading per leading digit or bracket would out-number the names under them."""
    window = browser(
        [
            _handle("2 Scene", section="main"),
            _handle("Alpha Scene", section="main"),
            _handle("[bracketed] Scene", section="main"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("main",))

    assert _labels(window.index) == [
        NON_LETTER_HEADING, "2 Scene", "[bracketed] Scene", "A", "Alpha Scene",
    ]


def test_the_way_back_is_not_in_the_sidebar(browser, tmp_path: Path):
    """It is not something the folder holds, and it files under no letter."""
    window = browser(
        [_handle("Alpha Scene", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("big_batch", "cuts"))

    assert _labels(window.grid) == ["back", "Alpha Scene"]
    assert _labels(window.index) == ["A", "Alpha Scene"]


def test_the_sidebar_follows_the_folder_that_is_open(browser, tmp_path: Path):
    window = browser(
        [
            _handle("Alpha Scene", section="big_batch"),
            _handle("Mike Scene", section="small_batch"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    assert _labels(window.index) == ["B", "big_batch", "S", "small_batch"]

    window.open_folder(("small_batch",))

    assert _labels(window.index) == ["M", "Mike Scene"]


def test_choosing_a_name_moves_the_grid_to_it(browser, tmp_path: Path):
    """The point of the index — the grid's selection lands on the name picked."""
    handles = [
        _handle("Zulu Scene", section="main"),
        _handle("Alpha Scene", section="main"),
    ]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("main",))

    window.index.setCurrentRow(_labels(window.index).index("Zulu Scene"))

    assert window.grid.rows[window.grid.currentRow()].title == "Zulu Scene"


def test_activating_a_name_in_the_sidebar_plays_that_video(browser, tmp_path: Path):
    picked: list[str] = []
    window = browser(
        [_handle("Alpha Scene", "C:/videos/alpha.mp4", section="main")],
        thumbnail_cache=tmp_path,
        on_pick=picked.append,
    )
    window.open_folder(("main",))

    window.index.itemActivated.emit(window.index.item(_labels(window.index).index("Alpha Scene")))

    assert picked == ["C:/videos/alpha.mp4"]


def test_activating_a_folder_in_the_sidebar_walks_into_it(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="big_batch")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.index.itemActivated.emit(window.index.item(_labels(window.index).index("big_batch")))

    assert window.windowTitle().endswith("big_batch")
    assert _labels(window.index) == ["A", "Alpha Scene"]


def test_backspace_from_the_sidebar_goes_back_up_too(browser, tmp_path: Path):
    """The key belongs to the browse, not to whichever half happens to have focus."""
    window = browser(
        [_handle("Alpha Scene", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("big_batch", "cuts"))

    window.index.keyPressEvent(_backspace())

    assert _labels(window.grid) == ["all folders", "cuts  (1)"]


def test_a_letter_group_holds_every_name_that_starts_with_it():
    """Case is not a group of its own: one A heading covers "alpha" and "Alpha"."""
    rows = [_handle("beta"), _handle("Alpha"), _handle("alpha two"), _handle("Beta Two")]

    assert alphabetical_index(rows) == [
        IndexLine("A"),
        IndexLine("Alpha", 1),
        IndexLine("alpha two", 2),
        IndexLine("B"),
        IndexLine("beta", 0),
        IndexLine("Beta Two", 3),
    ]


def test_a_long_title_stays_inside_the_sidebar(browser, tmp_path: Path):
    """It elides at the edge rather than dragging a horizontal scrollbar in.

    A list view lays its rows out to the widest size hint it was given, and
    library titles run several times the sidebar's width — so left alone the
    index grows sideways and has to be scrolled across to be read at all.
    """
    window = browser(
        [_handle("Example Studio - A Fabricated Scene Title Far Longer Than The Sidebar Is Wide")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("main",))
    window.resize(900, 500)
    window.show()

    try:
        assert window.index.visualItemRect(window.index.item(1)).width() <= SIDEBAR_WIDTH
        assert window.index.horizontalScrollBar().maximum() == 0, "nothing to scroll across"
    finally:
        window.close()


def test_the_grid_takes_the_focus_though_the_sidebar_stands_first(browser, tmp_path: Path):
    """The arrows and the type-ahead drive the browse, and both belong on the tiles."""
    window = browser(
        [_handle("Alpha Scene", section="main")], thumbnail_cache=tmp_path, on_pick=lambda _v: None,
    )

    assert window.focusWidget() is window.grid


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


def test_closing_the_browse_tells_the_process_it_is_over(browser, tmp_path: Path):
    """Nothing else can: Qt does not count a Tool window towards the last-window
    quit, so without this the picked video would sit in the result file with the
    bridge still blocked on a process that had nothing left to do."""
    ended: list[str] = []
    window = browser(
        [_handle("alpha scene")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _video: None,
        on_close=lambda: ended.append("over"),
    )
    window.show()

    window.close()

    assert ended == ["over"]


def test_picking_a_video_ends_the_browse_as_well_as_reporting_it(browser, tmp_path: Path):
    picked: list[str] = []
    ended: list[str] = []
    handles = [_handle("alpha scene", "C:/videos/alpha.mp4", section="main")]
    window = browser(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=picked.append,
        on_close=lambda: ended.append("over"),
    )
    window.open_folder(("main",))
    window.show()

    window.grid.itemActivated.emit(window.grid.item(window.grid.rows.index(handles[0])))

    assert picked == ["C:/videos/alpha.mp4"]
    assert ended == ["over"]


def test_a_still_is_scaled_to_fit_its_tile_and_never_stretched(browser, tmp_path: Path):
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

    window = browser(
        [_handle("tall scene", str(tall))], thumbnail_cache=cache, on_pick=lambda _v: None,
    )
    window.open_folder(("main",))
    drawn = window.grid.item(window.grid.rows.index(window.grid.rows[-1])).icon().pixmap(ICON_WIDTH, ICON_HEIGHT)

    assert drawn.height() == ICON_HEIGHT, "the long edge should meet the tile's edge"
    assert drawn.width() == round(ICON_HEIGHT * 60 / 160), "proportions must not change"
    assert drawn.width() < ICON_WIDTH, "the short edge stops before the other edge"


def test_a_folder_tile_shows_four_stills_in_a_grid(browser, tmp_path: Path):
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

    window = browser(handles, thumbnail_cache=cache, on_pick=lambda _v: None)
    drawn = window.grid.item(0).icon().pixmap(ICON_WIDTH, ICON_HEIGHT)

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


def test_a_folder_holding_one_video_gives_it_the_whole_tile(browser, tmp_path: Path):
    """No point quartering a tile around a single still."""
    cache = tmp_path / "cache"
    cache.mkdir()
    video = tmp_path / "only.mp4"
    video.write_bytes(b"\0")
    Image.new("RGB", (160, 90), (200, 40, 90)).save(thumbnail_path(video, cache), "JPEG")

    window = browser(
        [_handle("only scene", str(video), section="big_batch")],
        thumbnail_cache=cache,
        on_pick=lambda _v: None,
    )
    drawn = window.grid.item(0).icon().pixmap(ICON_WIDTH, ICON_HEIGHT)

    assert drawn.width() == ICON_WIDTH, "one still fills the tile as a video's would"
