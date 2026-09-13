"""The library browser hanging in the headset: what it shows, and what a press does."""
from __future__ import annotations

import threading
from unittest.mock import patch

import numpy as np
from app_support.threading_utils import wait_until
from PIL import Image, ImageFont
from shared_ui.palette import BG_PRIMARY, BG_SECONDARY, BLUE, hovered

from fun_time.library_handles import LibraryHandle
from fun_time.library_tree import SubFolder
from fun_time.thumbnail_cache import thumbnail_path
from fun_time_vr.library_panel import (
    CLOSE,
    GO_UP,
    LIBRARY_WIDTH_PX,
    NEXT_PAGE,
    PREV_PAGE,
    LibraryBrowse,
    LibraryShelf,
    LibraryStills,
    cached_or_extracted,
    label_of,
    library_actions,
    library_height,
    paint_library,
    tile_rects,
    wrap_label,
)


def _handle(title: str, *, section: str) -> LibraryHandle:
    return LibraryHandle(title=title, versions=(f"C:/videos/{title}.mp4",), section=section)


def _middle(rect) -> tuple[int, int]:
    return rect.x + rect.width // 2, rect.y + rect.height // 2


_TWO_FOLDERS = (
    ("Beta Scene", "big_batch/whole"),
    ("Excerpt 1", "big_batch/cuts"),
    ("alpha scene", "small_batch"),
)

_TWENTY_SCENES = tuple((f"Scene {index:02d}", "big_batch") for index in range(20))

_TWENTY_FOLDERS = tuple((f"Scene {index:02d}", f"batch {index:02d}") for index in range(20))


def _library(entries) -> list[LibraryHandle]:
    return [_handle(title, section=section) for title, section in entries]


def _browse(*, playing: str = ""):
    played: list[str] = []
    closed: list[bool] = []
    browse = LibraryBrowse(
        play=played.append, close=lambda: closed.append(True), playing=lambda: playing,
    )
    return browse, played, closed


def _names(browse) -> list[str]:
    return [tile.display_name for tile in browse.tiles]


def _opened(library=_TWO_FOLDERS, *, playing: str = ""):
    browse, played, closed = _browse(playing=playing)
    browse.stocked(_library(library))
    browse.showing(True)
    return browse, played, closed


class TestWalkingTheLibrary:
    def test_it_opens_on_the_librarys_own_folders(self):
        browse, _played, _closed = _opened()

        assert _names(browse) == ["big_batch", "small_batch"]

    def test_pressing_a_folder_opens_it(self):
        browse, _played, _closed = _opened()

        browse.press(*_middle(tile_rects()[0]))

        assert _names(browse) == ["whole", "cuts"]

    def test_back_goes_up_to_the_folder_holding_this_one(self):
        browse, _played, _closed = _opened()
        browse.press(*_middle(tile_rects()[0]))

        browse.press(*_middle(library_actions()[GO_UP]))

        assert _names(browse) == ["big_batch", "small_batch"]

    def test_a_big_folder_shows_a_page_of_it_and_the_next_page_shows_the_rest(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES)
        browse.press(*_middle(tile_rects()[0]))
        assert _names(browse) == [f"Scene {index:02d}" for index in range(15)]

        browse.press(*_middle(library_actions()[NEXT_PAGE]))

        assert _names(browse) == [f"Scene {index:02d}" for index in range(15, 20)]

    def test_the_pages_stop_at_either_end(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES)
        browse.press(*_middle(tile_rects()[0]))

        browse.press(*_middle(library_actions()[PREV_PAGE]))
        assert _names(browse)[0] == "Scene 00"

        for _ in range(3):
            browse.press(*_middle(library_actions()[NEXT_PAGE]))
        assert _names(browse)[0] == "Scene 15"

    def test_back_lands_on_the_page_holding_the_folder_it_came_from(self):
        browse, _played, _closed = _opened(_TWENTY_FOLDERS)
        browse.press(*_middle(library_actions()[NEXT_PAGE]))
        browse.press(*_middle(tile_rects()[0]))

        browse.press(*_middle(library_actions()[GO_UP]))

        assert _names(browse)[0] == "batch 15"

    def test_pressing_a_video_plays_it_and_puts_the_browse_away(self):
        browse, played, closed = _opened()

        browse.press(*_middle(tile_rects()[1]))
        browse.press(*_middle(tile_rects()[0]))

        assert played == ["C:/videos/alpha scene.mp4"]
        assert closed == [True]
        assert not browse.open


class TestWhereItOpens:
    def test_on_the_page_of_the_folder_the_main_player_is_playing_from(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES, playing="C:/videos/Scene 17.mp4")

        assert _names(browse)[0] == "Scene 15"
        assert browse.lit.title == "Scene 17"

    def test_on_the_playing_video_once_a_library_still_being_read_arrives(self):
        browse, _played, _closed = _browse(playing="C:/videos/Scene 17.mp4")
        browse.showing(True)

        browse.stocked(_library(_TWENTY_SCENES))

        assert _names(browse)[0] == "Scene 15"


class TestWhenItIsUp:
    def test_a_browse_it_put_away_stays_away_until_the_session_puts_it_away_too(self):
        browse, _played, _closed = _opened()
        browse.press(*_middle(tile_rects()[1]))
        browse.press(*_middle(tile_rects()[0]))

        browse.showing(True)
        assert not browse.open

        browse.showing(False)
        browse.showing(True)
        assert browse.open

    def test_a_browse_already_up_keeps_its_place_while_the_session_keeps_it_up(self):
        browse, _played, _closed = _opened(playing="C:/videos/alpha scene.mp4")
        browse.press(*_middle(library_actions()[GO_UP]))

        browse.showing(True)

        assert _names(browse) == ["big_batch", "small_batch"]


class _NoStills:
    def image(self, _preview: str):
        return None


_STILL_RED = (200, 30, 30)


class _RedStills:
    def image(self, _preview: str):
        return Image.new("RGB", (160, 90), _STILL_RED)


class TestWhatItDraws:
    def test_it_keeps_its_size_whatever_it_shows(self):
        reading, _played, _closed = _browse()
        reading.showing(True)
        root, _played, _closed = _opened(_TWENTY_SCENES)
        page, _played, _closed = _opened(_TWENTY_SCENES, playing="C:/videos/Scene 17.mp4")

        sizes = {paint_library(browse, _NoStills()).size for browse in (reading, root, page)}

        assert sizes == {(LIBRARY_WIDTH_PX, library_height())}

    def test_it_says_so_while_the_library_is_still_being_read(self):
        reading, _played, _closed = _browse()
        reading.showing(True)
        empty, _played, _closed = _opened(())

        assert not np.array_equal(
            np.asarray(paint_library(reading, _NoStills())),
            np.asarray(paint_library(empty, _NoStills())),
        )

    def test_the_video_it_opened_on_is_lit_in_the_family_blue(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES, playing="C:/videos/Scene 17.mp4")
        painted = np.asarray(paint_library(browse, _NoStills()))

        lit, other = tile_rects()[2], tile_rects()[3]
        assert tuple(painted[lit.y + lit.height // 2, lit.x + lit.width - 2, :3]) == BLUE
        assert tuple(painted[other.y + other.height // 2, other.x + other.width - 2, :3]) != BLUE

    def test_the_tile_under_the_pointer_is_lifted(self):
        browse, _played, _closed = _opened()
        rect = tile_rects()[0]

        def ground(hover):
            painted = np.asarray(paint_library(browse, _NoStills(), hover=hover))
            return tuple(painted[rect.y + rect.height // 2, rect.x + rect.width - 2, :3])

        assert ground(None) == BG_SECONDARY
        assert ground(_middle(rect)) == hovered(BG_SECONDARY)

    def test_a_still_that_has_arrived_is_on_its_tile(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES)
        browse.press(*_middle(tile_rects()[0]))
        rect = tile_rects()[0]

        def middle_of_the_picture(stills):
            painted = np.asarray(paint_library(browse, stills))
            return tuple(painted[rect.y + 53, rect.x + rect.width // 2, :3])

        assert middle_of_the_picture(_RedStills()) == _STILL_RED
        assert middle_of_the_picture(_NoStills()) != _STILL_RED

    def test_a_folder_is_pictured_with_four_of_its_videos_apart(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES)
        rect = tile_rects()[0]
        painted = np.asarray(paint_library(browse, _RedStills()))
        left, top, width, height = rect.x + 4, rect.y + 4, rect.width - 8, 99

        top_left = tuple(painted[top + height // 4, left + width // 4, :3])
        bottom_right = tuple(painted[top + 3 * height // 4, left + 3 * width // 4, :3])
        between = tuple(painted[top + height // 2, left + width // 2, :3])

        assert top_left == bottom_right == _STILL_RED
        assert between != _STILL_RED

    def test_a_tile_names_the_video_under_its_picture(self):
        rect = tile_rects()[0]

        def under_the_picture(title):
            browse, _played, _closed = _opened(((title, "big_batch"),))
            browse.press(*_middle(rect))
            painted = np.asarray(paint_library(browse, _NoStills()))
            return painted[rect.y + 4 + 99:rect.y + rect.height, rect.x:rect.x + rect.width, :3]

        assert not np.array_equal(under_the_picture("Scene One"), under_the_picture("Scene Two"))

    def test_no_way_back_is_drawn_at_the_librarys_own_folders(self):
        browse, _played, _closed = _opened()
        back = library_actions()[GO_UP]

        def where_back_would_be():
            painted = np.asarray(paint_library(browse, _NoStills()))
            return painted[back.y:back.y + back.height, back.x:back.x + back.width, :3]

        assert (where_back_would_be() == BG_PRIMARY).all()
        browse.press(*_middle(tile_rects()[0]))
        assert not (where_back_would_be() == BG_PRIMARY).all()

    def test_its_page_controls_and_its_close_are_always_drawn(self):
        browse, _played, _closed = _opened()
        painted = np.asarray(paint_library(browse, _NoStills()))

        for action in (PREV_PAGE, NEXT_PAGE, CLOSE):
            rect = library_actions()[action]
            area = painted[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width, :3]
            assert not (area == BG_PRIMARY).all(), action

    def test_the_header_says_which_folder_and_which_page_of_it(self):
        browse, _played, _closed = _opened(_TWENTY_SCENES)
        back, prev = library_actions()[GO_UP], library_actions()[PREV_PAGE]

        def header():
            painted = np.asarray(paint_library(browse, _NoStills()))
            return painted[back.y:back.y + back.height, back.x + back.width:prev.x, :3].copy()

        at_the_librarys_own_folders = header()
        browse.press(*_middle(tile_rects()[0]))
        first_page = header()
        browse.press(*_middle(library_actions()[NEXT_PAGE]))
        second_page = header()

        assert not np.array_equal(at_the_librarys_own_folders, first_page)
        assert not np.array_equal(first_page, second_page)


class TestTheNameUnderATile:
    _WIDTH = 176

    def test_a_name_that_fits_stays_on_one_line(self):
        assert wrap_label(ImageFont.load_default(13), "Scene One", self._WIDTH) == ["Scene One"]

    def test_a_long_name_takes_a_second_line_cut_at_its_tail(self):
        font = ImageFont.load_default(13)

        lines = wrap_label(font, "Jane Doe " + "and another long part " * 6, self._WIDTH)

        assert len(lines) == 2
        assert lines[0].startswith("Jane Doe")
        assert lines[1].endswith("…")
        assert all(font.getlength(line) <= self._WIDTH for line in lines)

    def test_one_word_wider_than_the_tile_is_cut_on_its_one_line(self):
        font = ImageFont.load_default(13)

        (line,) = wrap_label(font, "x" * 80, self._WIDTH)

        assert line.endswith("…")
        assert font.getlength(line) <= self._WIDTH

    def test_a_folder_says_how_much_is_in_it(self):
        assert label_of(SubFolder("big_batch", 20, ())) == "big_batch  (20)"


class TestTheStillsOnTheTiles:
    _PREVIEW = "C:/videos/Scene One.mp4"

    def test_a_still_is_fetched_off_the_thread_that_asked_and_is_there_once_collected(
        self, tmp_path,
    ):
        still_file = tmp_path / "still.png"
        Image.new("RGB", (160, 90), _STILL_RED).save(still_file)
        fetched_on: list[str] = []

        def fetch(_preview, _cache_dir):
            fetched_on.append(threading.current_thread().name)
            return still_file

        stills = LibraryStills(tmp_path, fetch=fetch)
        try:
            stills.want([self._PREVIEW])
            wait_until(lambda: stills.collect() or stills.image(self._PREVIEW) is not None,
                       timeout=10.0)
        finally:
            stills.close()

        assert stills.image(self._PREVIEW).getpixel((80, 45)) == _STILL_RED
        assert fetched_on != [threading.current_thread().name]

    def test_a_still_that_cannot_be_read_is_skipped_and_the_rest_still_arrive(self, tmp_path):
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"not a picture")
        good = tmp_path / "good.png"
        Image.new("RGB", (160, 90), _STILL_RED).save(good)
        files = {"C:/videos/broken.mp4": broken, self._PREVIEW: good}

        stills = LibraryStills(tmp_path, fetch=lambda preview, _cache_dir: files[preview])
        try:
            stills.want(["C:/videos/broken.mp4", self._PREVIEW])
            wait_until(lambda: stills.collect() or stills.image(self._PREVIEW) is not None,
                       timeout=10.0)
        finally:
            stills.close()

        assert stills.image("C:/videos/broken.mp4") is None

    def test_the_page_being_looked_at_is_fetched_before_the_pages_already_left(self, tmp_path):
        started, carry_on = threading.Event(), threading.Event()
        fetched: list[str] = []

        def fetch(preview, _cache_dir):
            fetched.append(preview)
            started.set()
            carry_on.wait(timeout=10.0)

        stills = LibraryStills(tmp_path, fetch=fetch)
        try:
            stills.want(["first page 1", "first page 2", "first page 3"])
            assert started.wait(timeout=10.0)
            stills.want(["second page 1", "second page 2"])
            carry_on.set()
            wait_until(lambda: len(fetched) == 5, timeout=10.0)
        finally:
            stills.close()

        assert fetched == [
            "first page 1", "second page 1", "second page 2", "first page 2", "first page 3",
        ]


class TestWhereAStillComesFrom:
    def test_a_still_the_cache_already_holds_comes_back_without_opening_the_video(
        self, tmp_path,
    ):
        video, cache = tmp_path / "Scene One.mp4", tmp_path / "stills"
        cached = thumbnail_path(video, cache)
        cached.parent.mkdir(parents=True)
        Image.new("RGB", (16, 9), _STILL_RED).save(cached, "JPEG")

        with patch("fun_time_vr.library_panel.prewarm_thumbnails",
                   side_effect=AssertionError("the video was opened")):
            assert cached_or_extracted(str(video), cache) == cached

    def test_a_still_the_cache_lacks_is_taken_the_way_the_satellites_warm_theirs(
        self, tmp_path,
    ):
        warmed = []

        with patch("fun_time_vr.library_panel.prewarm_thumbnails",
                   side_effect=lambda videos, cache_dir: warmed.append((list(videos), cache_dir))):
            assert cached_or_extracted("C:/videos/Scene One.mp4", tmp_path) is None

        assert warmed == [(["C:/videos/Scene One.mp4"], tmp_path)]


class TestReadingTheLibrary:
    def test_the_library_is_read_off_the_thread_that_asked_and_put_on_the_shelf(self):
        read_on: list[str] = []

        def read():
            read_on.append(threading.current_thread().name)
            return _library(_TWO_FOLDERS)

        shelf = LibraryShelf(read)
        wait_until(lambda: shelf.handles is not None, timeout=10.0)

        assert shelf.handles == tuple(_library(_TWO_FOLDERS))
        assert read_on != [threading.current_thread().name]

    def test_a_library_that_cannot_be_read_leaves_an_empty_shelf_not_one_reading_forever(self):
        def read():
            raise OSError("the drive the library is on went away")

        shelf = LibraryShelf(read)
        wait_until(lambda: shelf.handles is not None, timeout=10.0)

        assert shelf.handles == ()

    def test_its_close_puts_it_away_playing_nothing(self):
        browse, played, closed = _opened()

        browse.press(*_middle(library_actions()[CLOSE]))

        assert closed == [True]
        assert played == []
        assert not browse.open
