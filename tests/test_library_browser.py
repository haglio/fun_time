from __future__ import annotations

import os
import re
import string
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QIcon, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QStyle,
    QStyleOptionViewItem,
)
from shared_ui.colors import TEXT_MUTED, TEXT_PRIMARY
from shared_ui.icons import qt_icon
from shared_ui.spacing import BUTTON_ICON

from fun_time import library_browser, load_config
from fun_time.library_browser import (
    ICON_HEIGHT,
    ICON_WIDTH,
    NON_LETTER_HEADING,
    SIDEBAR_WIDTH,
    TOP_LEVEL_NAME,
    BrowseCallbacks,
    BrowseOpening,
    IndexLine,
    LibraryBrowserWindow,
    alphabetical_index,
    browse_library,
    load_browser_config,
    load_flick_browser_config,
    main,
    name_of,
    pick_file_for,
    previews_of,
    rows_needing_stills,
)
from fun_time.library_handles import LibraryHandle
from fun_time.library_tree import SubFolder
from fun_time.manifest import write_windows_bridge_manifest
from fun_time.thumbnail_cache import thumbnail_path
from tests.drive_fakes import files_that_never_answer, folders_listed
from tests.still_fakes import ExtractionsHeldAtTheFirst, still_extractions

pytestmark = pytest.mark.usefixtures("no_still_is_extracted_from_the_made_up_videos")


def _handle(title: str, *versions: str, section: str = "main") -> LibraryHandle:
    return LibraryHandle(
        title=title, versions=versions or (f"C:/videos/{title}.mp4",), section=section,
    )


THE_FOUR_EVENTS_OF_A_MOUSES_DOUBLE_CLICK = (
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonRelease,
    QEvent.Type.MouseButtonDblClick,
    QEvent.Type.MouseButtonRelease,
)


def a_double_click_as_the_mouse_sends_it(view: QListWidget, row: int) -> None:
    point = QPointF(view.visualItemRect(view.item(row)).center())
    for kind in THE_FOUR_EVENTS_OF_A_MOUSES_DOUBLE_CLICK:
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            kind, point, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ))


def _labels(view: QListWidget) -> list[str]:
    return [view.item(row).text() for row in range(view.count())]


def _letter_row(index, letter: str) -> int:
    return index.lines.index(IndexLine(letter))


def _shown_names(index) -> list[str]:
    return [
        index.item(row).text() for row, line in enumerate(index.lines)
        if not line.is_heading and not index.isRowHidden(row)
    ]


def _filed(index) -> list[str]:
    lines = index.lines
    return [
        line.label for row, line in enumerate(lines)
        if not line.is_heading or (row + 1 < len(lines) and not lines[row + 1].is_heading)
    ]


def _header_links(window) -> dict[str, str]:
    anchors = re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', window.header.text())
    return {label: href for href, label in anchors}


def _header_words(window) -> str:
    return window.header.plain_text()


def _click(view: QAbstractItemView, row: int) -> None:
    point = QPointF(view.visualItemRect(view.item(row)).center())
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            kind, point, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ))


def _backspace() -> QKeyEvent:
    return QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Backspace.value, Qt.KeyboardModifier.NoModifier,
    )


def _thumbnailed(window, rows: list[int]) -> bool:
    return all(not window.grid.item(row).icon().isNull() for row in rows)


def _until(condition, *, within_s: float = 10.0) -> None:
    deadline = time.monotonic() + within_s
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert condition()


@pytest.fixture
def browser(tmp_path):
    opened: list[LibraryBrowserWindow] = []

    def configured(handles, *, thumbnail_cache, callbacks, activate_on_click=False,
                   top_level_name=TOP_LEVEL_NAME):
        window = LibraryBrowserWindow(
            handles, thumbnail_cache=thumbnail_cache, callbacks=callbacks,
            activate_on_click=activate_on_click, top_level_name=top_level_name)
        opened.append(window)
        return window

    def build(handles, *, thumbnail_cache, on_pick, on_close=None, on_dismiss=None):
        return configured(
            handles, thumbnail_cache=thumbnail_cache,
            callbacks=BrowseCallbacks(on_pick=on_pick, on_close=on_close, on_dismiss=on_dismiss))

    build.configured = configured
    yield build
    for window in opened:
        window.close()


def test_a_browse_opens_on_the_librarys_own_folders(browser, tmp_path: Path):
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
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("big_batch",))

    assert window.grid.currentRow() == 1


def test_a_browse_opens_where_the_session_already_is(browser, tmp_path: Path):
    handles = [
        _handle("alpha scene", "C:/videos/small_batch/alpha.mp4", section="small_batch"),
        _handle("Beta Scene", "C:/videos/big_batch/beta.mp4", section="big_batch/whole"),
    ]
    window = browser(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_on("C:/videos/big_batch/beta.mp4")

    assert _header_words(window) == "Library / big_batch / whole"
    assert window.grid.rows[window.grid.currentRow()] == handles[1]


def test_a_browse_opens_with_the_index_standing_on_it_too(browser, tmp_path: Path):
    handles = [
        _handle("Alpha Scene", "C:/videos/main/alpha.mp4", section="main"),
        _handle("Mike Scene", "C:/videos/main/mike.mp4", section="main"),
        _handle("Zulu Scene", "C:/videos/main/zulu.mp4", section="main"),
    ]
    window = browser(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_on("C:/videos/main/zulu.mp4")

    assert window.index.currentItem().text() == "Zulu Scene"
    assert _shown_names(window.index) == ["Zulu Scene"]
    assert window.grid.rows[window.grid.currentRow()] == handles[2]


def test_the_rendition_playing_need_not_be_the_one_a_pick_would_play(browser, tmp_path: Path):
    handles = [
        _handle("alpha scene", "C:/videos/small_batch/alpha.mp4", section="small_batch"),
        _handle(
            "Beta Scene",
            "C:/videos/big_batch/3_good_to_go/beta_big.mp4",
            "C:/videos/big_batch/0 unsorted/beta.mp4",
            section="big_batch",
        ),
    ]
    window = browser(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_on("C:/videos/big_batch/0 unsorted/beta.mp4")

    assert window.grid.rows[window.grid.currentRow()] == handles[1]


def test_the_playing_path_is_matched_however_it_is_spelled(browser, tmp_path: Path):
    handles = [_handle("Beta Scene", "C:/Videos/Big_Batch/beta.mp4", section="big_batch")]
    window = browser(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_on("c:/videos/big_batch/BETA.MP4")

    assert window.grid.rows[window.grid.currentRow()] == handles[0]


def test_a_video_the_library_does_not_hold_opens_at_the_top(browser, tmp_path: Path):
    handles = [_handle("Beta Scene", "C:/videos/big_batch/beta.mp4", section="big_batch")]
    window = browser(
        handles,
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_on("C:/elsewhere/something.mp4")

    assert _header_words(window) == TOP_LEVEL_NAME
    assert all(isinstance(what, SubFolder) for what in window.grid.rows)


def test_a_browse_with_nothing_playing_opens_at_the_top(browser, tmp_path: Path):
    handles = [_handle("Beta Scene", "C:/videos/big_batch/beta.mp4", section="big_batch")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_on("")

    assert _header_words(window) == TOP_LEVEL_NAME
    assert all(isinstance(what, SubFolder) for what in window.grid.rows)


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
    thumbnailed = tmp_path / "alpha.mp4"
    thumbnailed.write_bytes(b"\0")
    bare = tmp_path / "beta.mp4"
    bare.write_bytes(b"\0")
    Image.new("RGB", (16, 9)).save(thumbnail_path(thumbnailed, cache), "JPEG")

    handles = [_handle("alpha scene", str(thumbnailed)), _handle("Beta Scene", str(bare))]
    window = browser(handles, thumbnail_cache=cache, on_pick=lambda _video: None)
    window.open_folder(("main",))

    thumbnailed_row, bare_row = (window.grid.rows.index(handle) for handle in handles)
    assert not window.grid.item(thumbnailed_row).icon().isNull()
    assert window.grid.item(bare_row).icon().isNull()
    assert rows_needing_stills(window.grid.rows, cache) == [bare_row]


def test_closing_the_browse_extracts_no_still_after_the_one_in_hand(
    browser, tmp_path: Path, monkeypatch,
):
    extractions = ExtractionsHeldAtTheFirst()
    monkeypatch.setattr(library_browser, "thumbnail_for", extractions)
    window = browser(
        [_handle("alpha scene"), _handle("beta scene")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _video: None,
    )
    assert extractions.holding.wait(10.0)

    window.close()
    extractions.let_every_extraction_end()

    assert len(extractions.asked) == 1
    assert not any(extraction.is_alive() for extraction in still_extractions())


def test_a_folder_opened_while_the_one_above_is_extracted_gets_every_still_of_its_own(
    browser, tmp_path: Path, monkeypatch,
):
    extractions = ExtractionsHeldAtTheFirst()
    monkeypatch.setattr(library_browser, "thumbnail_for", extractions)
    handles = [_handle(f"scene {name}") for name in ("alpha", "beta", "gamma", "delta", "epsilon")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _video: None)
    assert extractions.holding.wait(10.0)

    window.open_folder(("main",))
    extractions.let_go.set()

    _until(lambda: _thumbnailed(window, [window.grid.rows.index(handle) for handle in handles]))


def test_a_folder_opens_thumbnailed_on_a_drive_stuck_on_its_videos(browser, tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    folder = tmp_path / "vids"
    folder.mkdir()
    handles = []
    for name in ("alpha", "beta", "gamma"):
        video = folder / f"{name}.mp4"
        video.write_bytes(b"\0")
        Image.new("RGB", (16, 9)).save(thumbnail_path(video, cache), "JPEG")
        handles.append(_handle(f"{name} scene", str(video)))

    with files_that_never_answer(folder), folders_listed() as read:
        window = browser(handles, thumbnail_cache=cache, on_pick=lambda _video: None)
        window.open_folder(("main",))

    rows = [window.grid.rows.index(handle) for handle in handles]
    assert all(not window.grid.item(row).icon().isNull() for row in rows)
    assert len(read) == 2


def test_a_folder_tile_is_thumbnailed_with_a_still_from_inside_it(browser, tmp_path: Path):
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
    window = browser(
        [_handle("alpha scene")], thumbnail_cache=tmp_path, on_pick=lambda _video: None,
    )

    assert window.windowFlags() & Qt.WindowType.Tool


def test_the_folder_open_is_named_across_the_top(browser, tmp_path: Path):
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.resize(900, 600)

    window.open_folder(("big_batch", "cuts"))
    window.layout().activate()

    assert _header_words(window) == "Library / big_batch / cuts"
    assert window.header.geometry().top() == 0
    assert window.header.geometry().width() == window.width()
    assert window.header.height() <= window.index.geometry().top()
    assert window.header.height() <= window.grid.geometry().top()


def test_the_top_level_is_named_as_the_library(browser, tmp_path: Path):
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    assert _header_words(window) == TOP_LEVEL_NAME


def test_a_browse_of_genaus_flicks_is_named_for_genau_from_the_top_down(browser, tmp_path: Path):
    window = browser.configured(
        [_handle("scene one", section="VR")],
        thumbnail_cache=tmp_path,
        callbacks=BrowseCallbacks(on_pick=lambda _v: None),
        top_level_name="Genau",
    )
    at_the_top = _header_words(window)

    window.open_folder(("VR",))

    assert (at_the_top, _header_words(window)) == ("Genau", "Genau / VR")


def test_the_folder_you_are_in_is_named_but_not_a_link(browser, tmp_path: Path):
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("big_batch", "cuts"))

    assert list(_header_links(window)) == [TOP_LEVEL_NAME, "big_batch"]


def test_each_folder_above_the_one_open_is_a_link_back_to_it(browser, tmp_path: Path):
    window = browser(
        [_handle("Excerpt 1", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("big_batch", "cuts"))

    window.header.linkActivated.emit(_header_links(window)["big_batch"])

    assert _labels(window.grid) == ["all folders", "cuts  (1)"]


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

    assert _filed(window.index) == ["A", "Alpha Scene", "Another Scene", "B", "Beta Scene"]


def test_the_sidebar_is_alphabetical_where_the_grid_is_ranked(browser, tmp_path: Path):
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
    assert _filed(window.index) == ["B", "big_batch", "S", "small_batch"]


def test_a_heading_is_not_something_the_arrows_can_land_on(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="main")], thumbnail_cache=tmp_path, on_pick=lambda _v: None,
    )
    window.open_folder(("main",))
    letter = _letter_row(window.index, "A")

    heading, name = window.index.item(letter), window.index.item(letter + 1)

    assert heading.flags() == Qt.ItemFlag.NoItemFlags
    assert name.flags() & Qt.ItemFlag.ItemIsSelectable


def test_a_name_with_no_letter_to_file_under_goes_to_the_hash_heading(browser, tmp_path: Path):
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

    assert _filed(window.index) == [
        NON_LETTER_HEADING, "2 Scene", "[bracketed] Scene", "A", "Alpha Scene",
    ]


def test_the_way_back_is_not_in_the_sidebar(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("big_batch", "cuts"))

    assert _labels(window.grid) == ["back", "Alpha Scene"]
    assert _filed(window.index) == ["A", "Alpha Scene"]


def test_the_sidebar_follows_the_folder_that_is_open(browser, tmp_path: Path):
    window = browser(
        [
            _handle("Alpha Scene", section="big_batch"),
            _handle("Mike Scene", section="small_batch"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    assert _filed(window.index) == ["B", "big_batch", "S", "small_batch"]

    window.open_folder(("small_batch",))

    assert _filed(window.index) == ["M", "Mike Scene"]


def test_choosing_a_name_moves_the_grid_to_it(browser, tmp_path: Path):
    handles = [
        _handle("Zulu Scene", section="main"),
        _handle("Alpha Scene", section="main"),
    ]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("main",))

    window.index.setCurrentRow(_labels(window.index).index("Zulu Scene"))

    assert window.grid.rows[window.grid.currentRow()].title == "Zulu Scene"


def test_clicking_a_letter_moves_the_grid_to_where_that_letter_starts(
    browser, tmp_path: Path,
):
    handles = [_handle("Alpha Scene", section="main"), _handle("Zulu Scene", section="main")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.resize(900, 600)
    window.open_folder(("main",))

    _click(window.index, _letter_row(window.index, "Z"))

    assert window.grid.rows[window.grid.currentRow()].title == "Zulu Scene"
    assert window.index.currentItem().text() == "Zulu Scene"


def test_reopening_a_letter_takes_the_grid_back_after_it_has_wandered(
    browser, tmp_path: Path,
):
    handles = [_handle("Alpha Scene", section="main"), _handle("Zulu Scene", section="main")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.resize(900, 600)
    window.open_folder(("main",))
    _click(window.index, _letter_row(window.index, "Z"))
    window.grid.setCurrentRow(window.grid.rows.index(handles[0]))
    _click(window.index, _letter_row(window.index, "Z"))

    _click(window.index, _letter_row(window.index, "Z"))

    assert window.grid.rows[window.grid.currentRow()].title == "Zulu Scene"


def test_every_letter_is_listed_even_with_nothing_under_it(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="main"), _handle("Charlie Scene", section="main")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("main",))

    letters = [
        window.index.item(row).text().split()[0]
        for row, line in enumerate(window.index.lines) if line.is_heading
    ]
    assert letters == [NON_LETTER_HEADING, *string.ascii_uppercase]


def test_a_folder_opens_its_sidebar_on_the_letters_alone(browser, tmp_path: Path):
    window = browser(
        [
            _handle("Alpha Scene", section="big_batch"),
            _handle("Beta Scene", section="big_batch"),
            _handle("Mike Scene", section="small_batch"),
        ],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.resize(900, 600)
    _click(window.index, _letter_row(window.index, "B"))

    window.open_folder(("big_batch",))

    assert _shown_names(window.index) == []


def test_clicking_a_letter_opens_its_names_and_closes_the_letter_open_before(
    browser, tmp_path: Path,
):
    handles = [
        _handle("Alpha Scene", section="main"),
        _handle("Another Scene", section="main"),
        _handle("Beta Scene", section="main"),
    ]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.resize(900, 600)
    window.open_folder(("main",))

    _click(window.index, _letter_row(window.index, "A"))
    assert _shown_names(window.index) == ["Alpha Scene", "Another Scene"]

    _click(window.index, _letter_row(window.index, "B"))
    assert _shown_names(window.index) == ["Beta Scene"]


def test_a_letter_says_whether_it_is_open_closed_or_empty(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="main"), _handle("Charlie Scene", section="main")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.resize(900, 600)
    window.open_folder(("main",))

    _click(window.index, _letter_row(window.index, "A"))

    assert [(window.index.item(_letter_row(window.index, letter)).text(),
             _mark_on(window.index, letter)) for letter in "ABC"] == [
        ("A", "chevron_down"), ("B", None), ("C", "chevron_right"),
    ]


def _mark_on(index, letter: str) -> str | None:
    icon = index.item(_letter_row(index, letter)).icon()
    if icon.isNull():
        return None
    drawn = icon.pixmap(BUTTON_ICON).toImage()
    return next(name for name in ("chevron_down", "chevron_right")
                if qt_icon(name).pixmap(BUTTON_ICON).toImage() == drawn)


def test_a_letter_wears_its_mark_after_it_in_its_own_ink(browser, tmp_path: Path):
    window = browser([_handle("Alpha Scene", section="main")],
                     thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.open_folder(("main",))
    option = QStyleOptionViewItem()

    window.index.itemDelegate().initStyleOption(
        option, window.index.model().index(_letter_row(window.index, "A"), 0))

    assert option.decorationPosition == QStyleOptionViewItem.Position.Right
    assert option.state & QStyle.StateFlag.State_Enabled


def test_a_letter_with_nothing_under_it_is_grayed_out(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="main"), _handle("Charlie Scene", section="main")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )

    window.open_folder(("main",))

    color = {
        letter: window.index.item(_letter_row(window.index, letter)).foreground().color().name()
        for letter in "ABC"
    }
    assert color == {"A": TEXT_PRIMARY.name(), "B": TEXT_MUTED.name(), "C": TEXT_PRIMARY.name()}


def test_clicking_a_letter_with_nothing_under_it_changes_nothing(browser, tmp_path: Path):
    handles = [_handle("Alpha Scene", section="main"), _handle("Charlie Scene", section="main")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.resize(900, 600)
    window.open_folder(("main",))
    _click(window.index, _letter_row(window.index, "C"))

    _click(window.index, _letter_row(window.index, "B"))

    assert _shown_names(window.index) == ["Charlie Scene"]
    assert window.index.item(_letter_row(window.index, "B")).text() == "B"
    assert window.grid.rows[window.grid.currentRow()] == handles[1]


def test_clicking_the_open_letter_closes_it(browser, tmp_path: Path):
    handles = [_handle("Alpha Scene", section="main"), _handle("Beta Scene", section="main")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.resize(900, 600)
    window.open_folder(("main",))
    _click(window.index, _letter_row(window.index, "A"))

    _click(window.index, _letter_row(window.index, "A"))

    assert _shown_names(window.index) == []


def test_clicking_a_name_still_selects_it(browser, tmp_path: Path):
    handles = [_handle("Zebra Scene", section="main"), _handle("Zulu Scene", section="main")]
    window = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    window.resize(900, 600)
    window.open_folder(("main",))
    _click(window.index, _letter_row(window.index, "Z"))

    _click(window.index, _labels(window.index).index("Zulu Scene"))

    assert window.index.currentItem().text() == "Zulu Scene"
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

    assert _header_words(window) == "Library / big_batch"
    assert _filed(window.index) == ["A", "Alpha Scene"]


def test_backspace_from_the_sidebar_goes_back_up_too(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="big_batch/cuts")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("big_batch", "cuts"))

    window.index.keyPressEvent(_backspace())

    assert _labels(window.grid) == ["all folders", "cuts  (1)"]


def test_a_letter_group_holds_every_name_that_starts_with_it():
    rows = [_handle("beta"), _handle("Alpha"), _handle("alpha two"), _handle("Beta Two")]

    lines = alphabetical_index(rows)

    assert lines[lines.index(IndexLine("A")):lines.index(IndexLine("C"))] == [
        IndexLine("A"),
        IndexLine("Alpha", 1),
        IndexLine("alpha two", 2),
        IndexLine("B"),
        IndexLine("beta", 0),
        IndexLine("Beta Two", 3),
    ]


def test_names_that_sort_past_the_letters_still_file_under_the_one_hash():
    rows = [_handle("~tilde scene"), _handle("2 scene"), _handle("alpha")]

    lines = alphabetical_index(rows)

    assert lines.count(IndexLine(NON_LETTER_HEADING)) == 1
    start = lines.index(IndexLine(NON_LETTER_HEADING))
    assert lines[start:start + 3] == [
        IndexLine(NON_LETTER_HEADING), IndexLine("2 scene", 1), IndexLine("~tilde scene", 0),
    ]


def test_a_long_title_stays_inside_the_sidebar(browser, tmp_path: Path):
    window = browser(
        [_handle("Example Studio - A Fabricated Scene Title Far Longer Than The Sidebar Is Wide")],
        thumbnail_cache=tmp_path,
        on_pick=lambda _v: None,
    )
    window.open_folder(("main",))
    window.resize(900, 500)
    window.show()
    title_row = _letter_row(window.index, "E") + 1
    _click(window.index, title_row - 1)

    try:
        assert not window.index.isRowHidden(title_row)
        assert window.index.visualItemRect(window.index.item(title_row)).width() <= SIDEBAR_WIDTH
        assert window.index.horizontalScrollBar().maximum() == 0, "nothing to scroll across"
    finally:
        window.close()


def test_the_grid_takes_the_focus_though_the_sidebar_stands_first(browser, tmp_path: Path):
    window = browser(
        [_handle("Alpha Scene", section="main")], thumbnail_cache=tmp_path, on_pick=lambda _v: None,
    )

    assert window.focusWidget() is window.grid


def test_opening_the_browser_brings_it_in_front_of_the_funestras(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")

    with patch("fun_time.library_browser.LibraryBrowserWindow.show"), \
         patch("fun_time.library_browser.bring_to_front") as forward, \
         patch.object(QApplication, "exec", return_value=0):
        assert main([str(manifest), str(tmp_path / "pick.txt")]) == 0

    assert forward.call_count == 1


def test_the_browser_runs_on_an_application_made_where_it_is_let_go_of_safely(
        tmp_path: Path, monkeypatch):
    made = []

    @contextmanager
    def application(argv):
        made.append(argv)
        yield MagicMock(exec=MagicMock(return_value=7))

    monkeypatch.setattr(library_browser, "application", application)
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")

    with (patch("fun_time.library_browser.LibraryBrowserWindow"),
          patch("fun_time.library_browser.bring_to_front"),
          patch("fun_time.library_browser.dress_application") as dressed):
        assert main([str(manifest), str(tmp_path / "pick.txt")]) == 7

    assert made == [[]]
    dressed.assert_called_once()


def test_the_browser_reads_its_library_from_the_session_manifest(tmp_path: Path, cfg_factory):
    config = load_config(cfg_factory({"regen": {"metadata_root": str(tmp_path / "videos" / "metadata")}}))
    manifest = write_windows_bridge_manifest(config)

    browser_config = load_browser_config(manifest)

    assert browser_config.sources == "|".join(str(path) for path in config.paths.main_funestra_library_dirs)
    assert browser_config.metadata_root == tmp_path / "videos" / "metadata"
    assert browser_config.thumbnail_cache.parent == manifest.parent


def test_the_browser_reads_which_of_its_library_is_vr_from_the_session_manifest(
    tmp_path: Path, cfg_factory,
):
    config = load_config(cfg_factory({}))
    desktop = write_windows_bridge_manifest(config)
    headset = tmp_path / "headset" / "windows_bridge_launch.ini"
    headset.parent.mkdir()
    headset.write_text("[media]\nvr_library_dirs = D:/vr_one|D:/vr_two\n", encoding="utf-8")

    assert load_browser_config(desktop).vr_sources == ""
    assert load_browser_config(headset).vr_sources == "D:/vr_one|D:/vr_two"


def test_the_flick_browser_offers_the_vr_flicks_in_either_room(tmp_path: Path, cfg_factory):
    config = load_config(cfg_factory({}))
    desktop = write_windows_bridge_manifest(config)
    headset = tmp_path / "headset" / "windows_bridge_launch.ini"
    headset.parent.mkdir()
    headset.write_text("[media]\ngenau_flicks = D:/flicks\ngenau_vr_flicks = D:/flicks/VR\n",
                       encoding="utf-8")

    desktop_flicks = load_flick_browser_config(desktop)
    headset_flicks = load_flick_browser_config(headset)

    flicks = config.paths.flicks_dir
    assert (desktop_flicks.sources, desktop_flicks.vr_sources) == (
        f"{flicks / 'VR'}|{flicks / '2D'}", str(flicks / "VR"))
    assert (headset_flicks.sources, headset_flicks.vr_sources) == (
        f"{Path('D:/flicks') / 'VR'}|{Path('D:/flicks') / '2D'}", str(Path("D:/flicks") / "VR"))
    assert (desktop_flicks.top_level_name, desktop_flicks.metadata_root) == ("Genau", None)
    assert desktop_flicks.thumbnail_cache == load_browser_config(desktop).thumbnail_cache


def test_browsing_runs_the_browser_and_plays_what_was_picked(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        pick_file_for(manifest).write_text(r"C:\videos\beta.mp4", encoding="utf-8")

    picked = browse_library(manifest, r"C:\python.exe", BrowseOpening(over=(10, 20, 300, 400)),
                            runner=fake_run)

    assert picked == r"C:\videos\beta.mp4"
    assert commands == [[
        r"C:\python.exe", "-m", "fun_time.library_browser",
        str(manifest), str(pick_file_for(manifest)),
        "--x", "10", "--y", "20", "--width", "300", "--height", "400",
    ]]


def test_the_browser_is_told_what_the_session_has_up(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    browse_library(
        manifest, r"C:\python.exe", BrowseOpening(playing=r"C:\videos\beta.mp4"),
        runner=lambda command, **_k: commands.append(command),
    )

    assert commands[0][-2:] == ["--playing", r"C:\videos\beta.mp4"]


def test_a_browse_of_genaus_flicks_says_so_to_the_browser(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    browse_library(manifest, r"C:\python.exe", BrowseOpening(flicks=True),
                   runner=lambda command, **_k: commands.append(command))
    browse_library(manifest, r"C:\python.exe", BrowseOpening(),
                   runner=lambda command, **_k: commands.append(command))

    assert ["--genau" in command for command in commands] == [True, False]


def test_a_browse_runs_on_the_checkouts_the_session_runs(tmp_path: Path):
    checkout = tmp_path / "a-funestra-checkout"
    checkout.mkdir()
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text(f"[runtime]\nsibling_checkouts = {checkout}\n", encoding="utf-8")
    launched: list[dict] = []

    browse_library(manifest, r"C:\python.exe", BrowseOpening(),
                   runner=lambda _command, **kwargs: launched.append(kwargs))

    assert launched[0]["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(checkout)


def test_the_desktops_genau_browse_forks_into_vr_and_2d_with_the_ai_flicks_apart_in_2d(
    tmp_path: Path, cfg_factory,
):
    config = load_config(cfg_factory({}))
    for place in ("2D/AI/loop one.mp4", "2D/non_AI/scene one.mp4", "VR/scene two_180.mp4"):
        (config.paths.flicks_dir / place).parent.mkdir(parents=True, exist_ok=True)
        (config.paths.flicks_dir / place).touch()
    manifest = write_windows_bridge_manifest(config)

    with patch("fun_time.library_browser.LibraryBrowserWindow.show"), \
         patch("fun_time.library_browser.bring_to_front") as forward, \
         patch.object(QApplication, "exec", return_value=0):
        main([str(manifest), str(tmp_path / "pick.txt"), "--genau"])

    shown = forward.call_args.args[0]
    assert (_header_words(shown), _labels(shown.grid)) == ("Genau", ["VR  (1)", "2D  (2)"])
    shown.open_folder(("2D",))
    assert _labels(shown.grid)[1:] == ["AI  (1)", "non_AI  (1)"]


def test_a_browse_with_nothing_up_names_no_video(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    browse_library(manifest, r"C:\python.exe", BrowseOpening(), runner=lambda c, **_k: commands.append(c))

    assert "--playing" not in commands[0]


def test_an_abandoned_browse_picks_nothing(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")

    assert browse_library(manifest, r"C:\python.exe", BrowseOpening(), runner=lambda _c, **_k: None) is None


def test_an_abandoned_browse_never_replays_the_previous_pick(tmp_path: Path):
    manifest = tmp_path / "windows_bridge_launch.ini"
    manifest.write_text("", encoding="utf-8")
    pick_file_for(manifest).write_text(r"C:\videos\alpha.mp4", encoding="utf-8")

    assert browse_library(manifest, r"C:\python.exe", BrowseOpening(), runner=lambda _c, **_k: None) is None


def test_closing_the_browse_tells_the_process_it_is_over(browser, tmp_path: Path):
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


def test_a_lone_double_click_event_reaches_no_pick_the_mouses_four_do(browser, tmp_path: Path):
    handles = [_handle("alpha scene", "C:/videos/alpha.mp4", section="main")]
    lone_picked: list[str] = []
    picked: list[str] = []

    def shown(on_pick) -> LibraryBrowserWindow:
        window = browser(handles, thumbnail_cache=tmp_path, on_pick=on_pick)
        window.open_folder(("main",))
        window.resize(800, 500)
        window.show()
        QApplication.processEvents()
        return window

    lone = shown(lone_picked.append)
    row = lone.grid.rows.index(handles[0])
    point = QPointF(lone.grid.visualItemRect(lone.grid.item(row)).center())
    QApplication.sendEvent(lone.grid.viewport(), QMouseEvent(
        QEvent.Type.MouseButtonDblClick, point, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    a_double_click_as_the_mouse_sends_it(shown(picked.append).grid, row)

    assert lone_picked == []
    assert picked == ["C:/videos/alpha.mp4"]


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


def test_a_browse_driven_by_one_press_at_a_time_opens_what_is_pressed(browser, tmp_path: Path):
    handles = [_handle("alpha scene", section="main"), _handle("beta scene", section="other")]
    headset = browser.configured(
        handles, thumbnail_cache=tmp_path, callbacks=BrowseCallbacks(on_pick=lambda _v: None),
        activate_on_click=True)
    desktop = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    for window in (headset, desktop):
        window.show()

        _click(window.grid, window.grid.rows.index(next(
            row for row in window.grid.rows if isinstance(row, SubFolder) and row.name == "other")))

    assert _header_words(headset) == f"{TOP_LEVEL_NAME} / other"
    assert _header_words(desktop) == TOP_LEVEL_NAME


def test_a_browse_with_no_window_frame_closes_from_its_header(browser, tmp_path: Path):
    handles = [_handle("alpha scene", section="main")]
    dismissed: list[bool] = []
    headset = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None,
                      on_dismiss=lambda: dismissed.append(True))
    desktop = browser(handles, thumbnail_cache=tmp_path, on_pick=lambda _v: None)
    headset.show()
    desktop.show()

    assert desktop.dismiss_button is None
    button = headset.dismiss_button
    point = QPointF(button.rect().center())
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(button, QMouseEvent(
            kind, point, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        ))

    assert dismissed == [True]
    assert not headset.isVisible()


def _where_the_mark_lands(button) -> QRect:
    worn = button.grab().toImage()
    icon = button.icon()
    button.setIcon(QIcon())
    bare = button.grab().toImage()
    button.setIcon(icon)
    inked = [(x, y) for y in range(worn.height()) for x in range(worn.width())
             if worn.pixel(x, y) != bare.pixel(x, y)]
    xs, ys = [x for x, _ in inked], [y for _, y in inked]
    return QRect(QPoint(min(xs), min(ys)), QPoint(max(xs), max(ys)))


def test_the_close_mark_spans_more_than_half_its_button(browser, tmp_path: Path):
    headset = browser([_handle("alpha scene", section="main")], thumbnail_cache=tmp_path,
                      on_pick=lambda _v: None, on_dismiss=lambda: None)
    headset.show()
    button = headset.dismiss_button

    mark = _where_the_mark_lands(button)

    assert max(mark.width(), mark.height()) > button.height() / 2


def test_a_still_is_scaled_to_fit_its_tile_and_never_stretched(browser, tmp_path: Path):
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
    image = drawn.toImage()
    corners = {
        image.pixel(ICON_WIDTH // 4, ICON_HEIGHT // 4),
        image.pixel(3 * ICON_WIDTH // 4, ICON_HEIGHT // 4),
        image.pixel(ICON_WIDTH // 4, 3 * ICON_HEIGHT // 4),
        image.pixel(3 * ICON_WIDTH // 4, 3 * ICON_HEIGHT // 4),
    }
    assert len(corners) == 4


def test_a_folder_holding_one_video_gives_it_the_whole_tile(browser, tmp_path: Path):
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


class TestARowSaysWhatItIs:
    def test_a_video_and_a_folder_each_name_themselves(self):
        video = LibraryHandle(title="alpha scene", versions=("C:/v/a.mp4",),
                              section="big_batch")
        folder = SubFolder(name="big_batch", count=3, previews=())

        assert name_of(video) == "alpha scene"
        assert name_of(folder) == "big_batch"
        assert name_of(None) == ""

    def test_a_video_shows_one_still_and_a_folder_up_to_four(self):
        video = LibraryHandle(title="alpha scene", versions=("C:/v/a.mp4",),
                              section="big_batch")
        folder = SubFolder(name="big_batch", count=3,
                           previews=("C:/v/a.mp4", "C:/v/b.mp4"))

        assert previews_of(video) == ("C:/v/a.mp4",)
        assert previews_of(folder) == ("C:/v/a.mp4", "C:/v/b.mp4")
        assert previews_of(None) == ()
