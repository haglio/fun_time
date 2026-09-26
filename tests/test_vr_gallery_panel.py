"""The hosted app's own window, drawn on a screen in the room."""
from __future__ import annotations

from player_core.file_channel import consume_command_file

from fun_time_vr.frame_channel import FrameWriter
from fun_time_vr.gallery_panel import (
    FRAME_FILENAME,
    HOSTED_RELEASE,
    INPUT_FILENAME,
    TOKEN,
    WHEEL_NOTCH,
    GalleryPanel,
    event_line,
    hover_line,
    scroll_from_stick,
    scroll_line,
)
from fun_time_vr.pointer import DRAG, PRESS, RELEASE, PressEvent


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
