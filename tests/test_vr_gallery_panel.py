"""The hosted app's own window, drawn on a screen in the room."""
from __future__ import annotations

from player_core.file_channel import consume_command_file

from fun_time_vr.frame_channel import FrameWriter
from fun_time_vr.gallery_panel import (
    FRAME_FILENAME,
    HOSTED_RELEASE,
    INPUT_FILENAME,
    QUIET_S,
    TOKEN,
    WHEEL_NOTCH,
    GalleryPanel,
    event_line,
    hover_line,
    scroll_from_stick,
    scroll_line,
)
from fun_time_vr.pointer import DRAG, PRESS, RELEASE, RIGHT_CLICK, PressEvent


def _pixels(width: int, height: int, value: int) -> bytes:
    return bytes([value]) * (width * height * 4)


class TestWhereAPressLanded:
    """The room's pointer gives a fraction across the screen; the app is told the
    pixel of the picture it is looking at, since that is the only frame of
    reference the two share."""

    def test_a_press_is_placed_on_the_picture_that_is_up(self):
        event = PressEvent(kind=PRESS, screen="gallery", u=0.5, v=0.5)

        assert event_line(event, (800, 600)) == f"{PRESS} 400 300"

    def test_the_picture_is_measured_bottom_up_the_way_a_screen_is(self):
        """A room's v runs up from the screen's lower edge and a window's y runs
        down from its top, so the top of the gallery is v=1."""
        top = PressEvent(kind=PRESS, screen="gallery", u=0.0, v=1.0)

        assert event_line(top, (800, 600)) == f"{PRESS} 0 0"

    def test_a_release_carries_no_pixel_at_all(self):
        """It ends whatever the press began, wherever that was."""
        assert event_line(PressEvent(kind=RELEASE, screen="gallery"),
                          (800, 600)) == HOSTED_RELEASE

    def test_a_drag_is_placed_like_a_press(self):
        event = PressEvent(kind=DRAG, screen="gallery", u=1.0, v=0.0)

        assert event_line(event, (800, 600)) == f"{DRAG} 799 599"

    def test_a_right_click_is_placed_like_a_press(self):
        """A controller has one trigger, so the room sends the stick pushed in
        with it as a right-click -- at the pixel it was aimed at, like any
        other press."""
        event = PressEvent(kind=RIGHT_CLICK, screen="gallery", u=0.5, v=0.5)

        assert event_line(event, (800, 600)) == f"{RIGHT_CLICK} 400 300"


class TestTheChannelThePictureArrivesThrough:
    def test_the_last_picture_written_is_the_one_read(self, tmp_path):
        panel = GalleryPanel(tmp_path)
        writer = FrameWriter(tmp_path / FRAME_FILENAME, max_pixels=64)
        try:
            writer.write(TOKEN, 4, 2, _pixels(4, 2, 7))

            assert panel.frame() == (4, 2, _pixels(4, 2, 7))
        finally:
            writer.close()
            panel.close()

    def test_the_size_a_press_is_placed_on_is_the_size_that_arrived(self, tmp_path):
        """The app publishes the window at whatever size Qt gave it, so nothing
        here may assume one."""
        panel = GalleryPanel(tmp_path)
        writer = FrameWriter(tmp_path / FRAME_FILENAME, max_pixels=64)
        try:
            assert panel.size is None
            writer.write(TOKEN, 8, 4, _pixels(8, 4, 3))
            panel.frame()

            assert panel.size == (8, 4)

            panel.press(PressEvent(kind=PRESS, screen="gallery", u=0.5, v=0.5))
        finally:
            writer.close()
            panel.close()

        assert consume_command_file(tmp_path / INPUT_FILENAME,
                                    uppercase=False) == [f"{PRESS} 4 2"]

    def test_a_press_before_any_picture_is_dropped(self, tmp_path):
        """Nothing has said how big the window is, so there is no pixel to name
        -- and a guessed one aims the press somewhere else in the gallery."""
        panel = GalleryPanel(tmp_path)
        try:
            panel.press(PressEvent(kind=PRESS, screen="gallery", u=0.5, v=0.5))
        finally:
            panel.close()

        assert consume_command_file(tmp_path / INPUT_FILENAME, uppercase=False) == []


class TestSayingWhenTheAppHasGoneQuiet:
    """The room is the healthy side of this boundary: when the hosted app stops
    publishing and stops reading, the room is the only party that can still say
    so.  He met that silence as a window showing a picture from four hours
    earlier that answered no press, and nothing anywhere wrote a line about it.
    """

    def _panel(self, tmp_path):
        panel = GalleryPanel(tmp_path)
        writer = FrameWriter(tmp_path / FRAME_FILENAME, max_pixels=4)
        writer.write(TOKEN, 2, 2, _pixels(2, 2, 7))
        assert panel.frame(now=0.0) is not None
        return panel, writer

    def test_nothing_is_said_while_it_keeps_answering(self, tmp_path):
        panel, writer = self._panel(tmp_path)
        panel.send(hover_line(1, 1))

        writer.write(TOKEN, 2, 2, _pixels(2, 2, 9))
        panel.frame(now=QUIET_S * 2)

        assert panel.went_quiet(now=QUIET_S * 2) is None

    def test_a_silence_with_presses_waiting_is_said_once(self, tmp_path):
        panel, _writer = self._panel(tmp_path)
        panel.send(hover_line(1, 1))

        said = panel.went_quiet(now=QUIET_S + 1)

        assert said is not None and "1" in said
        assert panel.went_quiet(now=QUIET_S + 2) is None, "it said the same thing twice"

    def test_a_silence_with_nothing_waiting_is_not_a_silence(self, tmp_path):
        """A room nobody is pointing at sends nothing, and an app with nothing
        to answer publishes nothing: that pair is a quiet room, not a fault."""
        panel, _writer = self._panel(tmp_path)

        assert panel.went_quiet(now=QUIET_S + 1) is None


    def test_an_app_that_has_published_nothing_at_all_is_said_once(self, tmp_path):
        """A screen that has never had a picture is blank, which is a fault
        whether or not anyone has pointed at it: his 2026-10-08 session showed
        no Origenerator window in the room for four minutes and neither side
        wrote a line about it."""
        panel = GalleryPanel(tmp_path)
        assert panel.frame(now=0.0) is None

        said = panel.went_quiet(now=QUIET_S + 1)

        assert said is not None
        assert panel.went_quiet(now=QUIET_S + 2) is None, "it said the same thing twice"


class TestTheOtherWaysTheRoomReachesIt:
    def test_a_hover_carries_the_pixel_the_laser_rests_on(self):
        assert hover_line(12, 34) == "hover 12 34"

    def test_a_scroll_carries_the_notches_a_wheel_would_have_turned(self):
        assert scroll_line(-WHEEL_NOTCH) == f"scroll {-WHEEL_NOTCH}"

    def test_a_stick_inside_the_deadzone_scrolls_nothing(self):
        assert scroll_from_stick(0.01, 1.0) == 0.0

    def test_a_stick_held_over_scrolls_further_the_longer_it_is_held(self):
        held = scroll_from_stick(1.0, 1.0)

        assert held > 0
        assert scroll_from_stick(1.0, 2.0) == held * 2
