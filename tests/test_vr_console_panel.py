"""The console hanging in the headset: what goes on it, and how it is composed."""
from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
from PIL import ImageFont
from player_core.console import ConsoleModel
from player_core.console_hud import OSR2_ROBOT_HAND, ConsoleHud, ConsolePainter, ModeHud
from player_core.drive_layout import SPEED
from player_core.drive_readout import DriveHud
from player_core.hud_status import F_MODE_LABEL

from fun_time.event_log import FAVORITE, NOTICE
from fun_time_vr.console_panel import (
    NOTICE_STRIP_HEIGHT,
    PANEL_WIDTH_PX,
    PanelPointer,
    fit_notice,
    paint_notices,
    paint_panel,
    panel_hud,
    panel_painter,
)
from fun_time_vr.notices import KEPT, Notice


def _drive(**over) -> DriveHud:
    fields = dict(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                  advance_interval=10, waveform=tuple([0.5] * 80), trace_seconds=12.0)
    fields.update(over)
    return DriveHud(**fields)


def _engine_console(mode: str) -> ConsoleHud:
    """What Genau's engine composes: the clip's name on top, the room and the
    drive under it."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=ConsoleModel(mode=mode, broker=True, locked=False),
        drive=_drive(),
    )


class FakeGate:
    """What the panel asks of the drive gate, answered with a readout that can
    be told from the engine's."""

    def __init__(self) -> None:
        self.asked: list[DriveHud | None] = []

    def readout(self, published: DriveHud | None) -> DriveHud:
        self.asked.append(published)
        return _drive(speed=99)


def _hud(engine, *, gate=None, video_title="feature", clip_title="scene one", loading=None,
         f_mode=False, playback_speed=1.0):
    return panel_hud(engine, video_title=video_title, clip_title=clip_title,
                     loading=loading, drive_gate=gate or FakeGate(), f_mode=f_mode,
                     playback_speed=playback_speed)


class TestWhatThePanelNames:
    def test_under_a_video_the_top_line_is_the_videos_name(self):
        assert _hud(_engine_console("video")).modes.video == "feature"

    def test_in_genau_mode_it_is_the_clips(self):
        assert _hud(_engine_console("genau")).modes.video == "scene one"

    def test_a_clip_still_decoding_is_named_as_such(self):
        hud = _hud(_engine_console("genau"), loading="Loading scene two.mp4")

        assert hud.modes.video == "Loading scene two.mp4"

    def test_a_decode_does_not_rename_the_video(self):
        hud = _hud(_engine_console("video"), loading="Loading scene two.mp4")

        assert hud.modes.video == "feature"

    def test_the_room_under_the_top_line_is_the_engines(self):
        engine = _engine_console("video")

        assert _hud(engine).console == replace(engine.console, playback_speed=1.0)

    def test_the_rate_on_the_row_is_the_primarys_not_the_engines(self):
        """The row draws ConsoleModel.playback_speed, and the console the panel
        composes is Genau's engine's, where that field never leaves its default
        -- so the primary's rate has to be folded in or the readout says 1x
        however the video is actually playing."""
        hud = _hud(_engine_console("video"), playback_speed=1.5)

        assert hud.console.playback_speed == 1.5

    def test_the_rate_is_folded_in_in_genau_mode_too(self):
        """The rate belongs to the video, which waits paused under the clip and
        resumes at it -- so it is carried whether or not Genau is covering it,
        even though the genau console draws the clip seconds row instead."""
        hud = _hud(_engine_console("genau"), playback_speed=0.5)

        assert hud.console.playback_speed == 0.5

    def test_the_rate_survives_having_no_engine_console_at_all(self):
        assert _hud(None, playback_speed=2.0).console.playback_speed == 2.0

    def test_with_no_engine_console_the_panel_still_names_what_is_playing(self):
        """The broker has the room: no console of Genau's own, but a panel."""
        assert _hud(None).modes.video == "feature"


class TestFModeOnTheStatusLine:
    """The word for the switch the F key throws.

    The console model Fun Time publishes lights the F button, but the status
    line above it is read off the player DRAWING the console — Nau's own copy
    on the desktop, and here the main role's.  Left out, F-mode was a button
    that lit with nothing said beside it, and the key read as doing nothing.
    """

    def test_the_line_says_it_under_a_video(self):
        assert F_MODE_LABEL in _hud(_engine_console("video"), f_mode=True).status_line

    def test_the_line_leaves_it_out_when_it_is_off(self):
        assert F_MODE_LABEL not in _hud(_engine_console("video"), f_mode=False).status_line

    def test_it_is_the_main_players_flag_and_so_not_said_over_a_clip(self):
        """In genau mode the slot belongs to Genau's own two filters, which ride
        in the console the engine composed; the main player's playlist is not
        what is on screen and its narrowing is not what the line describes."""
        hud = _hud(_engine_console("genau"), f_mode=True)

        assert hud.modes.f_mode is False

    def test_genaus_own_filter_still_fills_the_slot_in_genau_mode(self):
        engine = replace(
            _engine_console("genau"),
            console=replace(_engine_console("genau").console, favorites_filter=True),
        )

        assert F_MODE_LABEL in _hud(engine, f_mode=False).status_line


class TestWhoseReadoutItDraws:
    def test_under_a_video_the_funscript_is_folded_into_the_engines_readout(self):
        """What the desktop's video-mode console draws: the script's green over
        Genau's blue, by the gate that holds the picture's forecasts."""
        engine, gate = _engine_console("video"), FakeGate()

        hud = _hud(engine, gate=gate)

        assert gate.asked == [engine.drive]
        assert hud.drive == _drive(speed=99)

    def test_with_nothing_published_the_gate_still_draws_the_script(self):
        gate = FakeGate()

        hud = _hud(None, gate=gate)

        assert gate.asked == [None]
        assert hud.drive == _drive(speed=99)

    def test_in_genau_mode_the_readout_is_genaus_own_motion(self):
        engine = _engine_console("genau")

        assert _hud(engine).drive is engine.drive

    def test_in_genau_mode_the_gate_is_told_nothing_was_published(self):
        """The video waits paused under the clip while Genau's wave moves on,
        so every forecast the gate held for it is void by the time the video
        is watched again."""
        gate = FakeGate()

        _hud(_engine_console("genau"), gate=gate)

        assert gate.asked == [None]


def _paint(mode="video", *, title="feature"):
    hud = _hud(_engine_console(mode), video_title=title, clip_title=title)
    return paint_panel(panel_painter(), hud)


class TestHowItIsComposed:
    def test_the_console_sits_under_the_announcement_strip(self):
        painted = _paint()
        console_rgba, (console_w, console_h) = panel_painter().rgba(_hud(_engine_console("video")))

        assert painted.size == (console_w, NOTICE_STRIP_HEIGHT + console_h)
        under = np.asarray(painted)[NOTICE_STRIP_HEIGHT:]
        assert np.array_equal(
            under, np.frombuffer(console_rgba, dtype=np.uint8).reshape(console_h, console_w, 4))

    def test_the_panel_is_that_and_nothing_else(self):
        """Every player draws its own scrubber and volume slider over its own
        picture, Genau's clip included, so none of that is repeated here."""
        assert _paint().height == NOTICE_STRIP_HEIGHT + panel_painter().rgba(
            _hud(_engine_console("video")))[1][1]


class TestTheAnnouncementStrip:
    """The desktop flashes a toast over the player and lists it in the log
    panel; both live in the dashboard, which a VR session never launches, so
    this is the only place the headset is told what it was heard to say."""

    _NOW = 100.0

    def _notices(self, *messages, level=NOTICE):
        return tuple(Notice(message, level, self._NOW) for message in messages)

    def test_it_keeps_its_height_with_nothing_to_say(self):
        """A strip that grew and shrank would rehang the console a little lower
        every time a command landed."""
        empty = paint_notices((), PANEL_WIDTH_PX)

        assert empty.size == (PANEL_WIDTH_PX, NOTICE_STRIP_HEIGHT)
        assert np.asarray(empty)[:, :, 3].max() == 0

    def test_a_notice_draws_on_it(self):
        painted = paint_notices(self._notices("landscape next"), PANEL_WIDTH_PX)

        assert painted.size == (PANEL_WIDTH_PX, NOTICE_STRIP_HEIGHT)
        assert np.asarray(painted)[:, :, 3].max() > 0

    def test_the_newest_is_lowest_nearest_the_console(self):
        one = np.asarray(paint_notices(self._notices("first"), PANEL_WIDTH_PX))
        two = np.asarray(paint_notices(self._notices("first", "second"), PANEL_WIDTH_PX))

        assert one[: NOTICE_STRIP_HEIGHT // 2, :, 3].max() == 0
        assert two[: NOTICE_STRIP_HEIGHT // 2, :, 3].max() > 0

    def test_a_louder_line_reads_a_different_color(self):
        """The log panel's own mapping: an ordinary announcement is white and an
        error red, so a line means the same thing in the headset as on the desk."""
        plain = np.asarray(paint_notices(self._notices("landscape next"), PANEL_WIDTH_PX))
        loud = np.asarray(
            paint_notices(self._notices("landscape next", level=logging.ERROR), PANEL_WIDTH_PX))

        assert not np.array_equal(plain, loud)

    def test_the_family_green_is_kept_for_its_own_family(self):
        plain = np.asarray(paint_notices(self._notices("clip locked"), PANEL_WIDTH_PX))
        favorite = np.asarray(
            paint_notices(self._notices("clip locked", level=FAVORITE), PANEL_WIDTH_PX))

        assert not np.array_equal(plain, favorite)

    def test_only_the_last_few_fit(self):
        many = self._notices(*[f"command {index}" for index in range(KEPT + 2)])

        assert paint_notices(many, PANEL_WIDTH_PX).size == (PANEL_WIDTH_PX, NOTICE_STRIP_HEIGHT)

    def test_a_long_report_is_cut_at_its_tail(self):
        """A voice report carries the phrase that missed, which is the whole
        reason to read it, so the head is what survives the cut."""
        font = ImageFont.load_default(12)
        cut = fit_notice(font, "unrecognized voice command: " + "word " * 40, 100)

        assert cut.startswith("unrecognized")
        assert cut.endswith("\u2026")
        assert font.getlength(cut) <= 100

    def test_a_line_that_fits_is_left_alone(self):
        font = ImageFont.load_default(12)

        assert fit_notice(font, "play", 500) == "play"


class TestItKeepsItsSize:
    """He saw the panel change size between the modes: the console sized itself
    to its rows, and the genau-mode ones are narrower.  A screen in a scene that
    changes size is a screen that moves."""

    def test_a_notice_does_not_resize_the_panel(self):
        quiet = _paint()
        speaking = paint_panel(
            panel_painter(), _hud(_engine_console("video")),
            notices=(Notice("landscape next", NOTICE, 100.0),),
        )

        assert speaking.size == quiet.size
        assert not np.array_equal(np.asarray(speaking), np.asarray(quiet))

    def test_the_two_modes_paint_the_same_size(self):
        video, genau = _paint("video"), _paint("genau")

        assert video.size == genau.size == (PANEL_WIDTH_PX, video.height)

    def test_a_long_title_does_not_widen_it(self):
        long_title = "Jane Doe - scene one - " + "a long descriptor " * 6

        assert _paint(title=long_title).size == _paint(title="short").size

    def test_the_held_width_clears_the_widest_mode(self):
        """Held narrower than its rows the painter widens the panel, and the
        two modes would differ again; the constant has to clear both."""
        for mode in ("video", "genau"):
            assert ConsolePainter().rgba(_hud(_engine_console(mode)))[1][0] <= PANEL_WIDTH_PX


def _live_console() -> ConsoleHud:
    """Video mode with the Robot Hand on the device, so the readout's bars take a press."""
    return ConsoleHud(
        modes=ModeHud(video="scene one"),
        console=ConsoleModel(mode="video", broker=True, locked=False, osr2=OSR2_ROBOT_HAND),
        drive=_drive(),
    )


class TestAPressOnThePanel:
    """The pointer's (u, v) on the hanging panel, turned into what the desktop's
    console does under a mouse: buttons post, bars are held and dragged, the
    chip sets the level, the scrubber seeks."""

    def _pointer(self):
        painter = panel_painter()
        panel = paint_panel(painter, _hud(_live_console(), clip_title=""))
        posted: list[str] = []
        pointer = PanelPointer(painter, post=posted.append)
        pointer.painted(panel.size)
        return SimpleNamespace(pointer=pointer, painter=painter, posted=posted, size=panel.size)

    @staticmethod
    def _uv(size, px: float, py: float) -> tuple[float, float]:
        width, height = size
        return (px + 0.5) / width, 1 - (py + 0.5) / height

    def _button_uv(self, p, action: str) -> tuple[float, float]:
        """A button's middle, in the PANEL's pixels: the painter places its
        buttons in the console's, which the strip above pushes down."""
        (x, y, w, h), _button = next(
            (rect, button) for rect, button in p.painter.buttons if button.action == action)
        return self._uv(p.size, x + w // 2, y + h // 2 + NOTICE_STRIP_HEIGHT)

    def test_a_button_posts_its_command(self):
        p = self._pointer()

        p.pointer.press(*self._button_uv(p, "main_lock"))
        p.pointer.release()

        assert p.posted == ["main_lock"]

    def test_a_press_that_lands_on_no_button_asks_for_nothing(self):
        """Neither the scrubber nor the volume is here any more; each rides on the
        player it belongs to, so a press off the buttons reaches nothing."""
        p = self._pointer()
        width, height = p.size

        p.pointer.press(*self._uv(p.size, width - 2, height - 2))

        assert p.posted == []

    def test_a_readouts_bar_is_held_and_dragged(self):
        p = self._pointer()
        speed = next(track for track in p.painter.tracks if track.axis == SPEED)
        x, y, w, h = speed.rect
        y += NOTICE_STRIP_HEIGHT  # the track is placed in the console's pixels

        p.pointer.press(*self._uv(p.size, x + w * 0.25, y + h / 2))
        p.pointer.drag(*self._uv(p.size, x + w * 0.25, y + h / 2))
        p.pointer.drag(*self._uv(p.size, x + w * 0.75, y + h / 2))
        p.pointer.release()
        p.pointer.drag(*self._uv(p.size, x + w * 0.5, y + h / 2))

        assert len(p.posted) == 2
        assert all(command.startswith("robot_hand_speed_") for command in p.posted)
        assert p.posted[0] != p.posted[1]

    def test_the_tooltip_stays_put_while_the_pointer_wanders_over_one_button(self):
        p = self._pointer()
        u, v = self._button_uv(p, "main_lock")
        nudge = 2 / p.size[0]

        anchor = p.pointer.tooltip_anchor((u, v))

        assert anchor is not None
        assert p.pointer.tooltip_anchor((u + nudge, v)) == anchor
        assert p.pointer.tooltip_anchor(self._button_uv(p, "main_next")) != anchor
        assert p.pointer.tooltip_anchor((0.5, -0.5)) is None
        assert p.pointer.tooltip_anchor(None) is None
