from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from fun_time_vr.reference_panel import (
    _PAD,
    NEXT_PAGE,
    PAGES_FOLDER,
    PREV_PAGE,
    ReferencePage,
    ReferencePages,
    ReferencePagesHost,
    ReferencePointer,
    ReferenceState,
    note_lines,
    page_of,
    paint_reference,
    read_pages,
    reference_actions,
    reference_size,
    scroll_limit,
    write_pages,
)

_WIDTH = 790
_TITLE = 20
_NOTE = ("A made-up note long enough to need more than one line when it is set in the "
         "reference's small type across the whole width of the panel, so it wraps at least once.")


def _page(title: str, height: int, *, shade: int = 90, note: str = "") -> ReferencePage:
    image = Image.new("RGBA", (_WIDTH, height), (24, 24, 24, 255))
    image.paste((40, 40, 220, 255), (0, 4, 200, _TITLE - 4))
    for y in range(_TITLE, height, 40):
        image.paste((shade, shade, shade, 255), (0, y, _WIDTH, y + 20))
    return ReferencePage(title, image, _TITLE, note)


def _with_a_mark_at(page: ReferencePage, row: int) -> ReferencePage:
    image = page.image.copy()
    image.paste((250, 10, 10, 255), (300, row, 340, row + 10))
    return ReferencePage(page.title, image, page.title_ends_at, page.note)


_SHORT = _page("Dashboard", 300)
_LONG = _page("Funestras", 3500, shade=140, note=_NOTE)
PAGES = ReferencePages((_SHORT, _LONG, _page("Origenerator", 500, shade=60)), step_px=60)


def _middle(action: str) -> tuple[int, int]:
    rect = reference_actions(PAGES)[action]
    return rect.x + rect.width // 2, rect.y + rect.height // 2


def _painted(state: ReferenceState, pages: ReferencePages = PAGES) -> np.ndarray:
    return np.asarray(paint_reference(state, pages))


def _red_marks(panel: np.ndarray) -> int:
    red = (panel[:, :, 0] > 200) & (panel[:, :, 1] < 60)
    return int(red.sum())


class TestItShowsTheDesktopsPages:
    def test_it_paints_what_a_section_says(self):
        first = _painted(ReferenceState(open=True, page=0))
        second = _painted(ReferenceState(open=True, page=1))

        assert first.shape == second.shape
        assert not np.array_equal(first, second)

    def test_it_keeps_its_size_across_the_sections(self):
        sizes = {paint_reference(ReferenceState(open=True, page=index), PAGES).size
                 for index in range(len(PAGES.pages))}

        assert sizes == {reference_size(PAGES)}

    def test_it_hangs_as_tall_for_its_width_as_it_always_has(self):
        width, height = reference_size(PAGES)

        assert width == _WIDTH + 2 * _PAD
        assert abs(height / width - 1012 / 760) < 0.01

    def test_the_pages_title_stays_at_its_head(self):
        title = (40, 40, 220)
        for scrolled in (0, 400):
            panel = _painted(ReferenceState(open=True, page=1, scrolled=scrolled))
            rows = np.nonzero((np.abs(panel[:, :, :3].astype(int) - title).sum(axis=2) < 30).any(axis=1))[0]
            assert rows.min() >= _PAD, scrolled
            assert rows.max() < _PAD + 28, scrolled

    def test_a_sections_note_is_painted_whole_on_as_many_lines_as_it_takes(self):
        lines = note_lines(_NOTE, PAGES)

        assert " ".join(lines) == " ".join(_NOTE.split())
        assert 2 <= len(lines) <= 3

    def test_no_page_runs_its_rows_past_its_lower_edge(self):
        for index in range(len(PAGES.pages)):
            for scrolled in (0, 10_000):
                panel = _painted(ReferenceState(open=True, page=index, scrolled=scrolled))
                lower_edge = panel[reference_size(PAGES)[1] - _PAD:, :, :3]
                assert not (lower_edge.max(axis=2) > 50).any(), (index, scrolled)


class TestScrollingAPage:
    def test_a_page_shorter_than_the_panel_does_not_scroll(self):
        assert scroll_limit(ReferenceState(open=True, page=0), PAGES) == 0

    def test_a_page_taller_than_the_panel_scrolls_as_far_as_its_last_row(self):
        limit = scroll_limit(ReferenceState(open=True, page=1), PAGES)

        assert 3500 - _TITLE - reference_size(PAGES)[1] < limit < 3500 - _TITLE

    def test_scrolling_brings_up_the_rows_below(self):
        marked = ReferencePages((_SHORT, _with_a_mark_at(_LONG, 2000), *PAGES.pages[2:]), PAGES.step_px)
        pointer = ReferencePointer(ReferenceState(open=True, page=1))

        assert _red_marks(_painted(pointer.state, marked)) == 0
        pointer.scroll(1800, marked)
        assert _red_marks(_painted(pointer.state, marked)) > 0

    def test_it_stops_at_the_top_and_the_bottom(self):
        pointer = ReferencePointer(ReferenceState(open=True, page=1))

        pointer.scroll(-50, PAGES)
        assert pointer.state.scrolled == 0
        pointer.scroll(100_000, PAGES)
        assert pointer.state.scrolled == scroll_limit(pointer.state, PAGES)

    def test_turning_the_page_starts_the_next_at_its_top(self):
        pointer = ReferencePointer(ReferenceState(open=True, page=1))
        pointer.scroll(500, PAGES)

        pointer.press(*_middle(NEXT_PAGE), PAGES)

        assert pointer.state.scrolled == 0


class TestWalkingTheSections:
    def test_the_controls_step_forward_and_back(self):
        pointer = ReferencePointer(ReferenceState(open=True))

        pointer.press(*_middle(NEXT_PAGE), PAGES)
        assert page_of(pointer.state, PAGES) == 1

        pointer.press(*_middle(PREV_PAGE), PAGES)
        assert page_of(pointer.state, PAGES) == 0

    def test_it_wraps_at_either_end(self):
        pointer = ReferencePointer(ReferenceState(open=True))

        pointer.press(*_middle(PREV_PAGE), PAGES)

        assert page_of(pointer.state, PAGES) == len(PAGES.pages) - 1

    def test_a_press_on_the_page_itself_does_nothing(self):
        pointer = ReferencePointer(ReferenceState(open=True))
        width, height = reference_size(PAGES)

        assert pointer.press(width // 2, height - 4, PAGES) is None
        assert page_of(pointer.state, PAGES) == 0


class TestWhenItIsUp:
    def test_the_session_says_so(self):
        pointer = ReferencePointer()

        pointer.showing(True)
        assert pointer.state.open

        pointer.showing(False)
        assert not pointer.state.open

    def test_opening_it_again_starts_at_the_top_of_the_front_page(self):
        pointer = ReferencePointer()
        pointer.showing(True)
        pointer.press(*_middle(NEXT_PAGE), PAGES)
        pointer.scroll(500, PAGES)
        pointer.showing(False)

        pointer.showing(True)

        assert (page_of(pointer.state, PAGES), pointer.state.scrolled) == (0, 0)

    def test_walking_it_does_not_close_it(self):
        pointer = ReferencePointer()
        pointer.showing(True)

        pointer.press(*_middle(NEXT_PAGE), PAGES)
        pointer.showing(True)

        assert pointer.state.open
        assert page_of(pointer.state, PAGES) == 1


class TestTheControls:
    def test_each_control_s_mark_spans_more_than_half_its_button(self):
        panel = _painted(ReferenceState(open=True, page=0))

        for action, rect in reference_actions(PAGES).items():
            button = panel[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width, :3]
            ys, xs = np.nonzero(button.max(axis=2) > 128)
            spans = max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
            assert spans > rect.width / 2, action


class TestThePagesOnDisk:
    def test_what_is_written_reads_back_the_same(self, tmp_path):
        write_pages(PAGES, tmp_path)

        read = read_pages(tmp_path)

        assert read.step_px == PAGES.step_px
        assert [(page.title, page.title_ends_at, page.note) for page in read.pages] == [
            (page.title, page.title_ends_at, page.note) for page in PAGES.pages]
        assert np.array_equal(np.asarray(read.pages[1].image), np.asarray(_LONG.image))

    def test_nothing_reads_until_the_list_of_pages_is_there(self, tmp_path):
        assert read_pages(tmp_path) is None


class _FakeProcess:
    def __init__(self, command, *, exits_with=None, **kwargs) -> None:
        self.command = command
        self.exits_with = exits_with
        self.ended: list[str] = []

    def poll(self):
        return self.exits_with

    def terminate(self):
        self.ended.append("terminated")

    def wait(self, timeout):
        return 0

    def kill(self):
        self.ended.append("killed")


class TestTheHelperThatDrawsThem:
    def _host(self, tmp_path: Path, launched: list, **process) -> ReferencePagesHost:
        def launch(command, **kwargs):
            launched.append(_FakeProcess(command, **process))
            return launched[-1]

        return ReferencePagesHost(state_dir=tmp_path, python_exe=r"C:\python.exe", launch=launch)

    def test_nothing_is_drawn_until_the_reference_is_first_wanted(self, tmp_path):
        launched: list = []

        self._host(tmp_path, launched)

        assert launched == []

    def test_the_first_ask_starts_one_helper_that_draws_the_pages_for_this_session(self, tmp_path):
        launched: list = []
        host = self._host(tmp_path, launched)

        assert host.pages() is None
        assert host.pages() is None

        (process,) = launched
        assert process.command[1:] == [
            "-m", "fun_time_vr.reference_pages",
            "--state-dir", str(tmp_path), "--out", str(tmp_path / PAGES_FOLDER)]

    def test_pages_left_by_an_earlier_session_are_never_shown(self, tmp_path):
        write_pages(PAGES, tmp_path / PAGES_FOLDER)
        host = self._host(tmp_path, [])

        assert host.pages() is None

    def test_the_pages_are_shown_once_the_helper_has_written_them(self, tmp_path):
        host = self._host(tmp_path, [])
        host.pages()

        write_pages(PAGES, tmp_path / PAGES_FOLDER)

        assert len(host.pages().pages) == len(PAGES.pages)

    def test_a_helper_that_failed_is_logged_once(self, tmp_path, caplog):
        host = self._host(tmp_path, [], exits_with=1)

        host.pages()
        host.pages()

        assert [record.levelname for record in caplog.records
                if "could not be drawn" in record.message] == ["WARNING"]

    def test_closing_ends_a_helper_still_drawing(self, tmp_path):
        launched: list = []
        host = self._host(tmp_path, launched)
        host.pages()

        host.close()

        assert launched[0].ended == ["terminated"]

    def test_closing_before_it_was_wanted_ends_nothing(self, tmp_path):
        self._host(tmp_path, []).close()
