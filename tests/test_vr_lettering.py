"""The lettering every panel in the headset shares: its face, and a line cut to fit."""
from __future__ import annotations

from PIL import ImageFont

from fun_time_vr.lettering import WORDMARK_FACE, fit_text, load_font


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


class TestTheFace:
    def test_a_face_this_machine_does_not_have_still_letters_the_line(self):
        font = load_font(14, face="no-such-face.ttf")

        assert font.getlength("play") > 0

    def test_a_panel_measuring_every_frame_reads_the_face_file_once(self):
        assert load_font(11) is load_font(11)
        assert load_font(11) is not load_font(11, face=WORDMARK_FACE)
