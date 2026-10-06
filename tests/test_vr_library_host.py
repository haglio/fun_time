"""The desktop's library browser, run with no window for the headset to show."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from PyQt6.QtCore import QPoint

from fun_time import library_browser
from fun_time.library_browser import CLIPS_TOP_LEVEL_NAME, TOP_LEVEL_NAME
from fun_time.library_handles import LibraryHandle
from fun_time_vr.frame_channel import FrameReader, FrameWriter
from fun_time_vr.library_host import (
    LISTING_FILENAME,
    HeadsetBrowse,
    read_the_library,
    serve_once,
)
from fun_time_vr.library_panel import LIBRARY_SIZE_PX


def _handle(title: str, section: str) -> LibraryHandle:
    return LibraryHandle(title=title, versions=(f"C:/videos/{title}.mp4",), section=section)


_LIBRARY = (
    [_handle(f"Scene {index:02d}", "VR") for index in range(40)]
    + [_handle("Alpha Scene", "2D/batch_one"), _handle("Beta Scene", "2D/batch_two")]
)


@pytest.fixture(autouse=True)
def _no_still_is_cut_from_the_made_up_videos(monkeypatch):
    monkeypatch.setattr(library_browser, "thumbnail_for", lambda *_args, **_kwargs: None)


@pytest.fixture
def clips_on_disk() -> list[LibraryHandle]:
    return [_handle("Loop One", "VR"), _handle("Loop Two", "2D/AI"),
            _handle("Scene Cut", "2D/non_AI")]


@pytest.fixture
def headset(tmp_path, clips_on_disk):
    frames = FrameWriter(tmp_path / "frame.bin", max_pixels=LIBRARY_SIZE_PX[0] * LIBRARY_SIZE_PX[1])
    reader = FrameReader(tmp_path / "frame.bin")
    said: list[str] = []
    browse = HeadsetBrowse(thumbnail_cache=tmp_path / "stills", frames=frames, say=said.append,
                           clips=lambda: list(clips_on_disk))
    browse.take_the_library(_LIBRARY)
    yield browse, reader, said
    browse.window.close()
    reader.close()
    frames.close()


def _center_of(widget, rect=None) -> QPoint:
    point = (rect or widget.rect()).center()
    return widget.mapTo(widget.window(), point)


def _press(browse, point: QPoint) -> None:
    browse.apply(f"press {point.x()} {point.y()}")
    browse.apply("release")


def _tile(browse, name: str) -> QPoint:
    grid = browse.window.grid
    row = next(row for row, what in enumerate(grid.rows) if what is not None and what.display_name == name)
    return _center_of(grid.viewport(), grid.visualItemRect(grid.item(row)))


def _names(browse) -> list[str]:
    return [what.display_name for what in browse.window.grid.rows if what is not None]


class TestOpening:
    def test_it_opens_on_the_folder_of_the_video_playing_with_that_video_chosen(self, headset):
        browse, _reader, _said = headset

        browse.apply("open 1 C:/videos/Scene 31.mp4")

        grid = browse.window.grid
        assert browse.window.isVisible()
        assert "Scene 31" in _names(browse)
        assert grid.rows[grid.currentRow()].title == "Scene 31"

    def test_it_opens_on_the_librarys_own_folders_with_nothing_playing(self, headset):
        browse, _reader, _said = headset

        browse.apply("open 1")

        assert _names(browse) == ["VR", "2D"]

    def test_what_it_shows_goes_out_for_that_opening_at_the_browsers_size(self, headset):
        browse, reader, _said = headset
        browse.apply("open 4")

        browse.publish(0.0)

        width, height, pixels = reader.latest(4)
        assert (width, height) == LIBRARY_SIZE_PX
        assert len(np.unique(np.frombuffer(pixels, np.uint8).reshape(-1, 4), axis=0)) > 1

    def test_nothing_new_goes_out_while_nothing_on_it_changes(self, headset):
        browse, reader, _said = headset
        browse.apply("open 4")
        browse.publish(0.0)
        reader.latest(4)

        browse.publish(1.0)

        assert reader.latest(4) is None

    def test_nothing_goes_out_while_it_is_put_away(self, headset):
        browse, reader, _said = headset

        browse.publish(0.0)

        assert reader.latest(0) is None


class TestPace:
    def test_an_idle_browse_looks_for_changes_ten_times_a_second_at_most(self, headset):
        browse, _reader, _said = headset
        browse.apply("open 1")
        grabs = []
        grab = browse.window.grab
        browse.window.grab = lambda: grabs.append(True) or grab()

        for now in (0.0, 0.03, 0.06, 0.12):
            browse.publish(now)

        assert len(grabs) == 2

    def test_a_browse_just_pressed_draws_at_once(self, headset):
        browse, _reader, _said = headset
        browse.apply("open 1")
        browse.publish(0.0)
        grabs = []
        grab = browse.window.grab
        browse.window.grab = lambda: grabs.append(True) or grab()

        browse.apply("hover 10 10")
        browse.publish(0.01)

        assert len(grabs) == 1


class TestPressing:
    def test_a_press_on_a_folder_opens_it(self, headset):
        browse, _reader, _said = headset
        browse.apply("open 1")

        _press(browse, _tile(browse, "2D"))

        assert _names(browse) == ["batch_one", "batch_two"]

    def test_a_press_on_a_video_says_which_and_puts_the_browse_away(self, headset):
        browse, _reader, said = headset
        browse.apply("open 1 C:/videos/Alpha Scene.mp4")

        _press(browse, _tile(browse, "Alpha Scene"))

        assert said == ["picked C:/videos/Alpha Scene.mp4"]
        assert not browse.window.isVisible()

    def test_a_press_on_its_close_says_so_and_puts_it_away_playing_nothing(self, headset):
        browse, _reader, said = headset
        browse.apply("open 1")

        _press(browse, _center_of(browse.window.dismiss_button))

        assert said == ["dismissed"]
        assert not browse.window.isVisible()

    def test_a_press_on_a_folder_named_in_the_header_goes_back_to_it(self, headset):
        browse, _reader, _said = headset
        browse.apply("open 1 C:/videos/Alpha Scene.mp4")
        header = browse.window.header
        assert header.text().startswith('<a href="0"')
        assert TOP_LEVEL_NAME in header.text()

        _press(browse, _center_of(header, header.rect().adjusted(0, 0, -header.width() + 60, 0)))

        assert _names(browse) == ["VR", "2D"]

    def test_a_press_on_a_letter_opens_its_names(self, headset):
        browse, _reader, _said = headset
        browse.apply("open 1")
        _press(browse, _tile(browse, "VR"))
        index = browse.window.index
        row = next(row for row, line in enumerate(index.lines) if line.label == "S")

        _press(browse, _center_of(index.viewport(), index.visualItemRect(index.item(row))))

        assert not index.isRowHidden(row + 1)


class TestScrolling:
    def test_the_stick_scrolls_what_the_pointer_is_over(self, headset):
        browse, _reader, _said = headset
        browse.apply("open 1 C:/videos/Scene 00.mp4")
        bar = browse.window.grid.verticalScrollBar()
        bar.setValue(0)
        over = _tile(browse, "Scene 00")
        browse.apply(f"hover {over.x()} {over.y()}")

        browse.apply("scroll -240")

        assert bar.value() > 0


class TestBrowsingGenausClips:
    def test_it_opens_on_the_folder_of_the_clip_genau_has_up_under_genaus_name(self, headset):
        browse, _reader, _said = headset

        browse.apply("clips 2 C:/videos/Loop Two.mp4")

        grid = browse.window.grid
        assert browse.window.isVisible()
        assert browse.window.header.text().endswith("AI")
        assert CLIPS_TOP_LEVEL_NAME in browse.window.header.text()
        assert grid.rows[grid.currentRow()].title == "Loop Two"

    def test_with_no_clip_up_it_opens_on_the_vr_and_2d_folders(self, headset):
        browse, _reader, _said = headset

        browse.apply("clips 2")

        assert _names(browse) == ["VR", "2D"]

    def test_the_2d_folder_keeps_the_ai_clips_apart_from_the_rest(self, headset):
        browse, _reader, _said = headset
        browse.apply("clips 2")

        _press(browse, _tile(browse, "2D"))

        assert _names(browse) == ["AI", "non_AI"]

    def test_a_clip_that_arrived_since_the_last_browse_is_there_the_next_time(
        self, headset, clips_on_disk,
    ):
        browse, _reader, _said = headset
        browse.apply("clips 1")
        clips_on_disk.append(_handle("Loop Three", "2D/AI"))

        browse.apply("clips 2 C:/videos/Loop Three.mp4")

        grid = browse.window.grid
        assert grid.rows[grid.currentRow()].title == "Loop Three"

    def test_a_clip_pressed_says_which_like_a_video(self, headset):
        browse, _reader, said = headset
        browse.apply("clips 1 C:/videos/Loop Two.mp4")

        _press(browse, _tile(browse, "Loop Two"))

        assert said == ["picked C:/videos/Loop Two.mp4"]
        assert not browse.window.isVisible()

    def test_the_library_opened_after_the_clips_is_the_library_again(self, headset):
        browse, _reader, _said = headset
        browse.apply("clips 1")

        browse.apply("open 2 C:/videos/Alpha Scene.mp4")

        assert "Alpha Scene" in _names(browse)
        assert TOP_LEVEL_NAME in browse.window.header.text()


class TestServing:
    def test_what_was_asked_while_the_library_was_being_read_is_done_once_it_is(
        self, headset, tmp_path: Path,
    ):
        browse, _reader, _said = headset
        asked = tmp_path / "input.txt"
        asked.write_text("open 1 C:/videos/Beta Scene.mp4\n", encoding="utf-8")

        assert serve_once(browse, asked, parent_alive=lambda: True, now=0.0) is True

        assert "Beta Scene" in _names(browse)

    def test_it_stops_once_the_headsets_player_is_gone(self, headset, tmp_path: Path):
        browse, _reader, _said = headset

        assert serve_once(browse, tmp_path / "input.txt", parent_alive=lambda: False,
                          now=0.0) is False


class TestWhileTheLibraryIsStillBeingRead:
    """Reading it is minutes of work when its drive is syncing.  It used to happen
    before the process served anything, so the headset's presses -- its close among
    them -- piled up in the input file unread (2026-09-20)."""

    @pytest.fixture
    def waiting(self, tmp_path):
        frames = FrameWriter(tmp_path / "frame.bin",
                             max_pixels=LIBRARY_SIZE_PX[0] * LIBRARY_SIZE_PX[1])
        said: list[str] = []
        browse = HeadsetBrowse(thumbnail_cache=tmp_path / "stills", frames=frames,
                               say=said.append, clips=lambda: [_handle("Loop One", "VR")])
        yield browse, said
        if browse.window is not None:
            browse.window.close()
        frames.close()

    def test_there_is_no_window_until_the_read_lands(self, waiting):
        browse, _said = waiting

        assert browse.window is None

    def test_every_press_and_hover_is_taken_and_answered_without_one(self, waiting, tmp_path):
        browse, said = waiting
        asked = tmp_path / "input.txt"
        asked.write_text("open 3 C:/videos/Beta Scene.mp4\nhover 5 5\npress 5 5\nrelease\n"
                         "scroll 120\n", encoding="utf-8")

        assert serve_once(browse, asked, parent_alive=lambda: True, now=0.0) is True

        assert not asked.exists()  # every line taken, none left to pile up
        assert said == []

    def test_the_opening_it_was_asked_for_happens_once_the_read_lands(self, waiting):
        browse, _said = waiting
        browse.apply("open 3 C:/videos/Beta Scene.mp4")

        browse.take_the_library(_LIBRARY)

        assert browse.window.isVisible()
        assert "Beta Scene" in _names(browse)

    def test_it_opens_at_the_token_it_was_asked_at_not_a_later_one(self, waiting, tmp_path):
        browse, _said = waiting
        reader = FrameReader(tmp_path / "frame.bin")
        browse.apply("open 7")

        browse.take_the_library(_LIBRARY)
        browse.publish(0.0)

        assert reader.latest(7) is not None
        reader.close()

    def test_genaus_clips_open_without_waiting_for_the_library(self, waiting):
        browse, _said = waiting

        browse.apply("clips 1")

        assert browse.window.isVisible()
        assert CLIPS_TOP_LEVEL_NAME in browse.window.header.text()

    def test_the_library_asked_for_after_the_clips_waits_for_its_read(self, waiting, tmp_path):
        browse, _said = waiting
        reader = FrameReader(tmp_path / "frame.bin")
        browse.apply("clips 1")
        browse.apply("open 2")

        browse.publish(0.0)

        assert reader.latest(2) is None
        reader.close()

    def test_nothing_is_published_before_the_read_lands(self, waiting, tmp_path):
        browse, _said = waiting
        reader = FrameReader(tmp_path / "frame.bin")
        browse.apply("open 1")

        browse.publish(0.0)

        assert reader.latest(1) is None
        reader.close()


class TestTheListingItKeeps:
    """Listing the folders is what costs the wait, so the last one is kept and
    answers the next browse at once."""

    def _config(self, tmp_path):
        return SimpleNamespace(
            sources="C:/videos/vr|C:/videos/flat", vr_sources="C:/videos/vr",
            metadata_root=tmp_path / "metadata", thumbnail_cache=tmp_path / "stills",
        )

    def test_the_first_read_lists_the_drive_and_the_next_does_not(self, tmp_path):
        listed: list[str] = []
        kept = tmp_path / LISTING_FILENAME
        with patch("fun_time_vr.library_host.collect_video_files",
                   side_effect=lambda sources: listed.append(sources) or []):
            read_the_library(self._config(tmp_path), kept)
            asked_once = list(listed)
            read_the_library(self._config(tmp_path), kept)

        assert asked_once, "the first read never listed anything"
        assert listed == asked_once

    def test_a_fresh_read_lists_it_again(self, tmp_path):
        listed: list[str] = []
        kept = tmp_path / LISTING_FILENAME
        with patch("fun_time_vr.library_host.collect_video_files",
                   side_effect=lambda sources: listed.append(sources) or []):
            read_the_library(self._config(tmp_path), kept)
            asked_once = list(listed)
            read_the_library(self._config(tmp_path), kept, afresh=True)

        assert listed == asked_once * 2

    def test_each_shelf_keeps_its_own_listing_so_neither_answers_the_others(self, tmp_path):
        kept = tmp_path / LISTING_FILENAME
        with patch("fun_time_vr.library_host.collect_video_files", return_value=[]):
            read_the_library(self._config(tmp_path), kept)

        assert len(list(tmp_path.glob(f"{kept.stem}_*.json"))) == 2
