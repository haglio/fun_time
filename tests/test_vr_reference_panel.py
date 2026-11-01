from __future__ import annotations

import numpy as np

from fun_time.command_reference import (
    CONTROLLER_HEADER,
    HOTKEY_HEADER,
    VOICE_HEADER,
    build_reference_sections,
)
from fun_time_vr.reference_panel import (
    _KEY_X,
    _PAD,
    _SAY_X,
    _WHAT_X,
    NEXT_PAGE,
    PREV_PAGE,
    REFERENCE_WIDTH_PX,
    ReferencePointer,
    ReferenceState,
    _cells,
    column_heads,
    header_lines,
    lines_of,
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
        assert page_of(ReferenceState(page=0)) == 0
        assert len(build_reference_sections()) > 1

    def test_it_paints_what_a_section_says(self):
        first = np.asarray(paint_reference(ReferenceState(open=True, page=0)))
        second = np.asarray(paint_reference(ReferenceState(open=True, page=1)))

        assert first.shape == second.shape
        assert not np.array_equal(first, second)

    def test_it_keeps_its_size_across_the_sections(self):
        sizes = {paint_reference(ReferenceState(open=True, page=index)).size
                 for index in range(len(build_reference_sections()))}

        assert sizes == {(REFERENCE_WIDTH_PX, reference_height())}

    def test_a_sections_note_is_painted_whole_on_as_many_lines_as_it_takes(self):
        noted = [section for section in build_reference_sections() if section.note]

        assert noted
        for section in noted:
            lines = note_lines(section)
            assert " ".join(lines) == " ".join(section.note.split())
            assert 1 <= len(lines) <= 3

    def test_a_shared_sections_hotkey_head_names_its_funestras_on_a_line_of_its_own(self):
        by_title = {section.title: section for section in build_reference_sections()}

        assert header_lines(by_title["Funestras"]) == 2
        assert header_lines(by_title["Dashboard"]) == 1
        assert header_lines(by_title["Genau"]) == 1

    def test_a_page_without_a_key_paints_the_voice_column_where_the_key_column_was(self):
        by_title = {section.title: section for section in build_reference_sections()}

        assert column_heads(by_title["Dashboard"]) == (
            (_KEY_X, HOTKEY_HEADER), (_SAY_X, VOICE_HEADER), (_WHAT_X, "What it does"))
        assert column_heads(by_title["Origenerator"]) == ((_KEY_X, VOICE_HEADER), (_WHAT_X, "What it does"))

    def test_a_page_with_a_controller_input_paints_a_controller_column_before_the_voice(self):
        by_title = {section.title: section for section in build_reference_sections()}

        assert [head for _, head in column_heads(by_title["Funestras"])] == [
            HOTKEY_HEADER, CONTROLLER_HEADER, VOICE_HEADER, "What it does"]
        assert [head for _, head in column_heads(by_title["VR"])] == [
            CONTROLLER_HEADER, VOICE_HEADER, "What it does"]
        for section in by_title.values():
            xs = [x for x, _ in column_heads(section)]
            assert xs == sorted(xs), section.title
            assert xs[0] == _KEY_X, section.title
            assert xs[-1] >= _WHAT_X, section.title
        nudge = next(row for row in by_title["Funestras"].rows if row.description == "nudge back 10 seconds")
        assert [text for _, text, _ in _cells(nudge, by_title["Funestras"])][:2] == ["— / — / -", "B / Y"]

    def test_an_untitled_group_takes_no_title_line(self):
        by_title = {section.title: section for section in build_reference_sections()}

        assert lines_of(by_title["VR"]) == len(by_title["VR"].rows)
        assert lines_of(by_title["Main Funestra"]) == 1 + len(by_title["Main Funestra"].rows)

    def test_no_page_runs_its_rows_past_its_lower_edge(self):
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
