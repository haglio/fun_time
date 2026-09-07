"""The hotkeys and voice reference, hanging in the headset."""
from __future__ import annotations

import numpy as np

from fun_time.command_reference import build_reference_sections
from fun_time_vr.reference_panel import (
    NEXT_PAGE,
    PREV_PAGE,
    REFERENCE_WIDTH_PX,
    ReferencePointer,
    ReferenceState,
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
