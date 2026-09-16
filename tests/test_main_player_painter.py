"""One painted frame: what the main player puts on top of mpv's video, and in what order.

Five things are drawn every frame -- the timeline along the lower edge, the time
readout at its left, the console in the top-left corner, the volume chip above the
timeline's right-hand end, and the loop's two frames above their marks -- and the
order they are built in is load-bearing in ways nothing was watching: the
heatmap's color row is built at the inset track's width and framed at the
window's, the room's two files are read before anything drawn believes them, and
the overlay ids are the z-order rather than the call order.

The player here is a spy rather than mpv: what reaches it is a list of overlay
calls, which is exactly what a frame is.
"""
from __future__ import annotations

import numpy as np
from player_core.console import ConsoleModel
from player_core.console_hud import ConsolePainter, ModeHud
from player_core.drive_readout import DriveHud
from player_core.funscript import Funscript
from player_core.playhead import PlayheadHudPainter, readout_xy, video_playhead
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x
from player_core.volume import VolumeHud

from main_player.overlay import HeatmapStrip, LoopThumbCapture

WIN_W, WIN_H = 1000, 600
TRACK_W = bar_track_x(WIN_W)[1] - bar_track_x(WIN_W)[0]
VIDEO = "gamma reel.mp4"
LOOP = (2000, 4000)


class SpyPlayer:
    """mpv's overlay surface, recorded: every call in order, and what is up."""

    def __init__(self, frame=None) -> None:
        self.calls: list[tuple] = []
        self.up: dict[int, np.ndarray] = {}
        self._frame = frame
        self.frame_rate = 30.0

    def overlay(self, ident: int, x: int, y: int, bgra) -> None:
        self.calls.append(("overlay", ident, x, y))
        self.up[ident] = bgra

    def remove_overlay(self, ident: int) -> None:
        self.calls.append(("remove", ident))
        self.up.pop(ident, None)

    def screenshot_bgra(self):
        return self._frame

    @property
    def ids(self) -> list[int]:
        return [call[1] for call in self.calls]


class SpyRoom:
    """What the room published, and when it was asked for it."""

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.console = ConsoleModel()
        self.drive = DriveHud()

    def refresh(self) -> None:
        self._log.append("refresh")


class SpyGate:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.told_the_device_drives_itself: list[bool] = []

    def readout(self, published, *, device_drives_itself: bool = False) -> DriveHud:
        self._log.append("readout")
        self.told_the_device_drives_itself.append(device_drives_itself)
        return published if published is not None else DriveHud()


class FakeModes:
    hud = ModeHud(video="gamma reel", length_mode="mixed", compilation="",
                  position=1, total=3, f_mode=False)


class FakeVolume:
    hud = VolumeHud()


class FakeSession:
    def __init__(self, *, scripted: bool = True, bounds=None) -> None:
        self.current_video = VIDEO
        self.current_funscript = (
            Funscript(actions=[(0, 0), (1000, 100), (2000, 0)]) if scripted else None)
        self.duration_ms = 4000.0
        self.position_ms = 2000.0
        self.speed = 1.0
        self.loop_bounds = bounds
        self.loop_state = "looping" if bounds is not None else "normal"
        self.record_in_ms = None
        self.showing_picture = False


def _frame(height: int = 10, width: int = 20):
    return np.zeros((height, width, 4), dtype=np.uint8)


def _console(session, log, *, osr2: str = "robot_hand"):
    from main_player.painter import ConsolePanel
    room = SpyRoom(log)
    room.console = ConsoleModel(osr2=osr2)
    return ConsolePanel(session, room=room, drive_gate=SpyGate(log),
                        console_hud=ConsolePainter(), modes=FakeModes())


def _painter(session, *, player=None, log=None, thumbs=None, heatmap=None):
    from main_player.painter import Painter
    log = [] if log is None else log
    player = player or SpyPlayer()
    painter = Painter(
        player, session, _console(session, log),
        heatmap=heatmap or HeatmapStrip(),
        volume=FakeVolume(),
        loop_thumbs=thumbs or LoopThumbCapture(),
    )
    return painter, player


def _paint(painter) -> None:
    painter.paint(WIN_W, WIN_H, hover=None)


class TestWhatOneFramePutsUp:
    def test_the_overlays_a_frame_owns(self):
        """Ids 0, 1, 6 and 7 every frame, each at its own place -- the readout's,
        1, is measured from its text, and TestTheReadout says where; the loop's
        two are 4 and 5, which is why a frame with no loop takes them down rather
        than leaving them.  A set, not a list: the ids are the z-order, so the
        order these go up in is not the contract -- see the next case for the
        one ordering that is."""
        painter, player = _painter(FakeSession())

        _paint(painter)

        readout = {call for call in player.calls if call[:2] == ("overlay", 1)}
        assert len(readout) == 1
        assert set(player.calls) - readout == {
            ("overlay", 0, 0, 576), ("overlay", 6, 8, 8), ("overlay", 7, 878, 577),
            ("remove", 4), ("remove", 5),
        }

    def test_the_timeline_is_measured_before_the_things_that_sit_above_it(self):
        """The chip and the loop's frames are both placed against the timeline
        row's height, and that height is whatever the heatmap was last updated
        to.  Measured before the update, they sit against the previous video's
        row for a frame."""
        painter, player = _painter(FakeSession())

        _paint(painter)

        assert player.ids.index(0) < player.ids.index(7)

    def test_the_timeline_is_built_at_the_track_width_and_framed_at_the_window(self):
        """Two widths, deliberately: the color row fills the inset track, and
        the strip it is framed into spans the window so it lines up with the
        plain bar.  Build the row at the window's width and the strip is drawn
        at the wrong scale, silently."""
        heatmap = HeatmapStrip()
        painter, player = _painter(FakeSession(), heatmap=heatmap)

        _paint(painter)

        assert len(heatmap.colors) == TRACK_W
        assert player.up[0].shape[1] == WIN_W

    def test_a_picture_has_no_timeline_so_the_frame_takes_the_bar_down(self):
        session = FakeSession(scripted=False)
        session.showing_picture = True
        painter, player = _painter(session)

        _paint(painter)

        assert ("remove", 0) in player.calls
        assert 0 not in player.up

    def test_an_unscripted_video_gets_the_plain_bar_in_the_same_place(self):
        """Every video has a clickable timeline; without a funscript there is no
        heatmap to build one from, so the shared progress bar stands in."""
        painter, player = _painter(FakeSession(scripted=False))

        _paint(painter)

        assert ("overlay", 0, 0, 576) in player.calls


class TestWhatTheBlankHasToTakeDown:
    def test_every_id_a_frame_draws_is_one_the_blank_knows_about(self):
        """When the room gives the main player's rect to Genau, main_player.display puts a black
        overlay up and takes down the ids it was handed -- `HUD_OVERLAYS`.  An
        id drawn here that is not in that tuple is one the black cannot cover,
        and it stays painted over the blackout for the rest of the session.

        Both directions matter, so this is an equality: a sixth overlay added
        without extending the tuple fails, and a tuple entry nothing draws any
        more fails too.
        """
        from main_player.display import _OVERLAY_ID
        from main_player.painter import HUD_OVERLAYS
        session = FakeSession(bounds=LOOP)
        painter, player = _painter(session, player=SpyPlayer(_frame()))

        _paint(painter)                     # the in frame is grabbed and drawn
        session.position_ms = 3700.0
        _paint(painter)                     # ...and then the out frame

        assert set(player.ids) == set(HUD_OVERLAYS)
        assert max(HUD_OVERLAYS) < _OVERLAY_ID, "the black would go underneath"


class TestTheConsolePanel:
    def test_the_room_is_read_before_the_motion_is_believed(self):
        """The console and the motion arrive as two files somebody else
        publishes.  Read after the readout, both the pill and the drawn line
        would be a frame late for the room they describe."""
        log: list[str] = []

        _console(FakeSession(), log).bgra(hover=None)

        assert log == ["refresh", "readout"]

    def test_the_gate_is_told_when_the_device_is_running_itself(self):
        """In auto mode the OSR2 drives on its own firmware and the script's
        T-Code is dropped at the broker, so folding the script into the picture
        draws a plan for a device nothing here has."""
        panel = _console(FakeSession(), [], osr2="auto")

        panel.bgra(hover=None)

        assert panel._drive_gate.told_the_device_drives_itself == [True]

    def test_the_gate_composes_the_script_in_every_other_state(self):
        panel = _console(FakeSession(), [], osr2="funscript")

        panel.bgra(hover=None)

        assert panel._drive_gate.told_the_device_drives_itself == [False]

    def test_a_frame_reads_the_room_exactly_once(self):
        """It is two file reads a frame, on a channel Genau and Fun Time are
        republishing while this polls it."""
        log: list[str] = []
        painter, _player = _painter(FakeSession(), log=log)

        _paint(painter)

        assert log.count("refresh") == 1


class TestTheLoopsOwnTwoFrames:
    """A running loop shows the frame it starts on and the frame it ends on,
    above their marks.  The frames come from mpv screenshots, which is why they
    are asked for one at a time: the in frame when the loop opens, the out frame
    only as playback nears the out point.
    """

    def test_the_first_frame_is_grabbed_and_drawn_when_the_loop_opens(self):
        """What is drawn is what mpv handed back, not merely something the
        painter put in both places."""
        grabbed = _frame()
        painter, player = _painter(FakeSession(bounds=LOOP),
                                   player=SpyPlayer(grabbed))

        _paint(painter)

        assert player.up[4] is grabbed

    def test_the_last_frame_joins_it_as_the_loop_comes_round(self):
        session = FakeSession(bounds=LOOP)
        painter, player = _painter(session, player=SpyPlayer(_frame()))

        _paint(painter)
        session.position_ms = 3700.0        # inside the out frame's lead
        _paint(painter)

        assert {4, 5} <= set(player.up)

    def test_a_frame_mpv_could_not_give_is_not_kept_as_one(self):
        """mpv answers None while it has no picture to give -- between videos,
        or before the first frame has been decoded.  Stored anyway, the capture
        reads as done and the frame it is holding is nothing."""
        thumbs = LoopThumbCapture()
        painter, player = _painter(FakeSession(bounds=LOOP),
                                   player=SpyPlayer(None), thumbs=thumbs)

        _paint(painter)

        assert thumbs.in_thumb is None
        assert not {4, 5} & set(player.up)

    def test_the_end_of_a_loop_takes_both_frames_off_the_screen(self):
        """The ids are stable so each frame updates in place, which is also why
        they have to be removed by hand: left alone, the thumbnails of a
        canceled loop stay over the video for the rest of the session."""
        session = FakeSession(bounds=LOOP)
        painter, player = _painter(session, player=SpyPlayer(_frame()))
        _paint(painter)

        session.loop_bounds, session.loop_state = None, "normal"
        _paint(painter)

        assert [c for c in player.calls if c[0] == "remove"] == [
            ("remove", 4), ("remove", 5)]


class TestTheReadout:
    def test_it_goes_up_against_the_start_of_the_track(self):
        player = SpyPlayer()
        painter, _player = _painter(FakeSession(scripted=False), player=player)

        _paint(painter)

        pill = PlayheadHudPainter().bgra(video_playhead(2000.0, 4000.0, 30.0))
        at = readout_xy(pill.shape[1], win_w=WIN_W, win_h=WIN_H, timeline_h=TIMELINE_HEIGHT)
        shown = [ident for kind, ident, *xy in player.calls
                 if kind == "overlay" and tuple(xy) == at]
        assert shown and np.array_equal(player.up[shown[-1]], pill)

    def test_a_video_whose_length_is_not_known_yet_takes_its_readout_down(self):
        session = FakeSession(scripted=False)
        session.duration_ms = 0.0
        painter, player = _painter(session)

        _paint(painter)

        assert ("remove", 1) in player.calls
