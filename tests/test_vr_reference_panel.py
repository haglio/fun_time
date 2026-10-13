"""The hotkeys and voice reference, hanging in the headset."""
from __future__ import annotations

import numpy as np

from fun_time.command_reference import build_reference_sections
from fun_time_vr.reference_panel import (
    _PAD,
    NEXT_PAGE,
    PREV_PAGE,
    REFERENCE_WIDTH_PX,
    ReferencePointer,
    ReferenceState,
    header_lines,
    note_lines,
    page_of,
    paint_reference,
    reference_actions,
    reference_height,
)


def _middle(action: str) -> tuple[int, int]:
    rect = reference_actions()[action]
    return rect.x + rect.width // 2, rect.y + rect.height // 2


class TestItIsTheSameReference:
    def test_it_shows_the_sections_the_desktop_shows(self):
        """One source of truth: the popup's own sections, painted rather than
        written out as HTML, so neither can drift from the vocabulary."""
        assert page_of(ReferenceState(page=0)) == 0
        assert len(build_reference_sections()) > 1

    def test_it_paints_what_a_section_says(self):
        first = np.asarray(paint_reference(ReferenceState(open=True, page=0)))
        second = np.asarray(paint_reference(ReferenceState(open=True, page=1)))

        assert first.shape == second.shape
        assert not np.array_equal(first, second)

    def test_it_keeps_its_size_across_the_sections(self):
        """A screen in a scene that changes size is a screen that moves, and the
        sections are nowhere near the same length."""
        sizes = {paint_reference(ReferenceState(open=True, page=index)).size
                 for index in range(len(build_reference_sections()))}

        assert sizes == {(REFERENCE_WIDTH_PX, reference_height())}

    def test_a_sections_note_is_painted_whole_on_as_many_lines_as_it_takes(self):
        """The satellite sections' notes run well past one line of the panel,
        and a note cut off at the first line loses the rule it carries."""
        noted = [section for section in build_reference_sections() if section.note]

        assert noted
        for section in noted:
            lines = note_lines(section)
            assert " ".join(lines) == " ".join(section.note.split())
            assert 1 <= len(lines) <= 3

    def test_a_shared_sections_hotkey_head_names_its_funestras_on_a_line_of_its_own(self):
        """Where the desktop's Hotkey head spans one column per Funestra, the
        panel gives their names a line under the head, and every page keeps
        that line's height so walking never resizes."""
        by_title = {section.title: section for section in build_reference_sections()}

        assert header_lines(by_title["Funestras"]) == 2
        assert header_lines(by_title["Satellite Funestras - Kino"]) == 2
        assert header_lines(by_title["Dashboard"]) == 1

    def test_no_page_runs_its_rows_past_its_lower_edge(self):
        """A longer note pushes the rows down, and the page has to grow with
        it rather than paint the last rows off its edge."""
        for index in range(len(build_reference_sections())):
            panel = np.asarray(paint_reference(ReferenceState(open=True, page=index)))
            lower_edge = panel[reference_height() - _PAD:, :, :3]

            assert not (lower_edge.max(axis=2) > 128).any(), index


class TestWalkingTheSections:
    def test_the_controls_step_forward_and_back(self):
        pointer = ReferencePointer(ReferenceState(open=True))

        pointer.press(*_middle(NEXT_PAGE))
        assert page_of(pointer.state) == 1

        pointer.press(*_middle(PREV_PAGE))
        assert page_of(pointer.state) == 0

    def test_it_wraps_at_either_end(self):
        last = len(build_reference_sections()) - 1
        pointer = ReferencePointer(ReferenceState(open=True))

        pointer.press(*_middle(PREV_PAGE))

        assert page_of(pointer.state) == last

    def test_a_press_on_the_page_itself_does_nothing(self):
        """It is a table to read; only the two controls act."""
        pointer = ReferencePointer(ReferenceState(open=True))

        assert pointer.press(REFERENCE_WIDTH_PX // 2, reference_height() - 4) is None
        assert page_of(pointer.state) == 0


class TestWhenItIsUp:
    def test_the_session_says_so(self):
        pointer = ReferencePointer()

        pointer.showing(True)
        assert pointer.state.open

        pointer.showing(False)
        assert not pointer.state.open

    def test_opening_it_again_starts_at_the_front(self):
        """Left on the last section, it would open there — and what he asked for
        is the reference, not where he happened to stop reading it."""
        pointer = ReferencePointer()
        pointer.showing(True)
        pointer.press(*_middle(NEXT_PAGE))
        pointer.showing(False)

        pointer.showing(True)

        assert page_of(pointer.state) == 0

    def test_walking_it_does_not_close_it(self):
        pointer = ReferencePointer()
        pointer.showing(True)

        pointer.press(*_middle(NEXT_PAGE))
        pointer.showing(True)

        assert pointer.state.open
        assert page_of(pointer.state) == 1


class TestTheControls:
    def test_each_control_s_mark_spans_more_than_half_its_button(self):
        panel = np.asarray(paint_reference(ReferenceState(open=True, page=0)))

        for action, rect in reference_actions().items():
            button = panel[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width, :3]
            ys, xs = np.nonzero(button.max(axis=2) > 128)
            spans = max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
            assert spans > rect.width / 2, action
