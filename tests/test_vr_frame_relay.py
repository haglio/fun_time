"""Which picture the frame loop may show, and which texture a video may paint into.

Every player paints on a thread and a GL context of its own, so the two sides
have to agree on three textures without either waiting on the other.  This is
that agreement, apart from the GL calls it is made of.
"""
from __future__ import annotations

from fun_time_vr.frame_relay import FrameRelay, capped_size


def test_there_is_nothing_to_show_before_the_first_picture():
    assert FrameRelay().take() is None


def test_a_painted_picture_is_shown_once_and_carries_the_size_it_was_painted_at():
    relay = FrameRelay()

    relay.painted(0, texture=7, width=1920, height=1080)

    picture = relay.take()
    assert (picture.slot, picture.texture, picture.width, picture.height) == (0, 7, 1920, 1080)
    assert relay.take() is None


def test_a_newer_picture_waits_until_the_one_being_copied_is_let_go():
    """The frame loop's copy runs on the graphics card after the call returns, so
    the texture it copies out of is off limits until the card says it is done."""
    relay = FrameRelay()
    relay.painted(0, texture=7, width=640, height=480)
    relay.take()

    relay.painted(1, texture=8, width=640, height=480)
    assert relay.take() is None

    relay.copied()
    assert relay.take().slot == 1


def test_the_video_never_paints_over_the_picture_the_loop_is_copying():
    relay = FrameRelay()
    relay.painted(0, texture=7, width=640, height=480)
    relay.take()
    relay.painted(1, texture=8, width=640, height=480)

    assert relay.slot_to_paint() == 2


def test_there_is_always_somewhere_to_paint_the_next_picture():
    """What the third texture is for: with the newest picture waiting to be taken
    and another being copied, a video that had only two would have to stop
    painting until the frame loop let one go."""
    relay = FrameRelay()
    for number in range(20):
        slot = relay.slot_to_paint()
        relay.painted(slot, texture=slot, width=640, height=480)
        if number % 3 == 0:
            relay.take()
        if number % 3 == 2:
            relay.copied()


def test_a_video_wider_than_the_cap_is_painted_smaller_at_its_own_shape():
    assert capped_size((3840, 2160), 2048) == (2048, 1152)


def test_a_video_inside_the_cap_is_painted_at_its_own_size():
    assert capped_size((1280, 720), 2048) == (1280, 720)


def test_there_is_no_size_to_paint_until_mpv_says_what_the_video_is():
    """Between files mpv reports (0, 0), and a target sized from that would be
    the teardown flicker the last picture is held through."""
    assert capped_size((0, 0), 2048) is None
