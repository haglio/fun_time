from __future__ import annotations

from PIL import ImageFont

from fun_time_vr.lettering import WORDMARK_FACE, fit_text, load_font, wrap_text


class TestALineCutToFit:
    def test_a_line_that_fits_is_left_alone(self):
        font = ImageFont.load_default(12)

        assert fit_text(font, "play", 500) == "play"

    def test_a_line_that_does_not_is_cut_at_its_tail(self):
        """A notice leads with what it is about, so the head survives the cut."""
        font = ImageFont.load_default(12)
        cut = fit_text(font, "unrecognized voice command: " + "word " * 40, 100)

        assert cut.startswith("unrecognized")
        assert cut.endswith("…")
        assert font.getlength(cut) <= 100


class TestLinesWrappedToFit:
    def test_a_line_too_wide_for_one_goes_on_to_the_next_with_every_word_kept(self):
        font = ImageFont.load_default(12)
        said = "not sure enough of: portrait next (press Enter to accept)"

        lines = wrap_text(font, said, 150, max_lines=3)

        assert len(lines) > 1
        assert " ".join(lines) == said
        assert all(font.getlength(line) <= 150 for line in lines)

    def test_what_will_not_fit_in_the_lines_it_may_take_is_cut_at_the_last(self):
        font = ImageFont.load_default(12)

        lines = wrap_text(font, "word " * 60, 150, max_lines=3)

        assert len(lines) == 3
        assert lines[-1].endswith("…")
        assert all(font.getlength(line) <= 150 for line in lines)

    def test_a_word_wider_than_a_whole_line_is_cut_to_it(self):
        font = ImageFont.load_default(12)

        [line] = wrap_text(font, "w" * 80, 150, max_lines=3)

        assert font.getlength(line) <= 150


class TestTheFace:
    def test_a_face_this_machine_does_not_have_still_letters_the_line(self):
        font = load_font(14, face="no-such-face.ttf")

        assert font.getlength("play") > 0

    def test_a_panel_measuring_every_frame_reads_the_face_file_once(self):
        assert load_font(11) is load_font(11)
        assert load_font(11) is not load_font(11, face=WORDMARK_FACE)
