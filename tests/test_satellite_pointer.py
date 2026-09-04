"""Where a press on a satellite's window lands.

The three controls overlap in principle — the chip sits inside the scrubber's
row, and a tall HUD can reach the bottom of a short window — so what these pin is
the ORDER, against the real geometry of all three: the chip's placement, the
scrubber's inset track, and the row's height, each read from the module that
draws it rather than restated here.
"""
from __future__ import annotations

from player_core.timeline import TIMELINE_HEIGHT
from player_core.volume import CHIP_H, chip_xy

from satellite.pointer import Pointer
from satellite.volume import SatelliteVolume
from tests.satellite_fakes import make_satellite_session

WIN_W, WIN_H = 640, 480
DURATION_MS = 5_000.0

_VX, _VY = chip_xy(win_w=WIN_W, win_h=WIN_H, timeline_h=TIMELINE_HEIGHT)
SPEAKER = (_VX + 7, _VY + CHIP_H // 2)
CHIP_HALFWAY = (_VX + 66, _VY + CHIP_H // 2)

# bar_track_x(640) spans 40..508: the midpoint of the track, and a point past its
# right-hand end that the chip does not cover.
BAR_MIDPOINT = (274, WIN_H - 4)
PAST_BAR_END = (512, WIN_H - 4)
BAR_START = (40, WIN_H - 4)
ON_THE_VIDEO = (300, 200)


class _StubHud:
    """The lock HUD's half of the pointer's interface, and nothing else."""

    def __init__(self, *, suppressed: bool = False) -> None:
        self.display_suppressed = suppressed
        self.presses: list[tuple[int, int]] = []
        self.motions: list[tuple[int, int]] = []

    def press(self, x: int, y: int) -> None:
        self.presses.append((x, y))

    def motion(self, x: int, y: int) -> None:
        self.motions.append((x, y))


def _pointer(tmp_path, *, suppressed: bool = False, hud: bool = True):
    session, player = make_satellite_session(tmp_path, duration_ms=DURATION_MS)
    volume = SatelliteVolume(player)
    stub = _StubHud(suppressed=suppressed) if hud else None
    return Pointer(session=session, volume=volume, hud=stub), player, stub


def _press(pointer, point) -> None:
    pointer.press(*point, win_w=WIN_W, win_h=WIN_H)


def _motion(pointer, point, *, held: bool) -> None:
    pointer.motion(*point, held=held, win_w=WIN_W, win_h=WIN_H)


class TestTheScrubber:
    def test_a_press_halfway_along_the_track_seeks_to_the_middle(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path)

        _press(pointer, BAR_MIDPOINT)

        assert player.seeks == [DURATION_MS / 2]

    def test_a_press_at_the_track_s_start_seeks_to_the_beginning(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path)

        _press(pointer, BAR_START)

        assert player.seeks == [0.0]

    def test_a_press_past_the_track_s_end_saturates_at_the_end(self, tmp_path):
        # The track stops short of the chip; the gap between them belongs to the
        # bar, and a press there asks for the end rather than for nothing.
        pointer, player, _hud = _pointer(tmp_path)

        _press(pointer, PAST_BAR_END)

        assert player.seeks == [DURATION_MS]

    def test_a_press_on_the_video_seeks_nothing_and_reaches_the_hud(self, tmp_path):
        # A satellite's paused state is the flag file's, re-read every pass, so
        # there is nothing for a press on the video itself to do here.
        pointer, player, hud = _pointer(tmp_path)

        _press(pointer, ON_THE_VIDEO)

        assert player.seeks == []
        assert hud.presses == [ON_THE_VIDEO]

    def test_a_satellite_with_no_hud_still_seeks(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path, hud=False)

        _press(pointer, BAR_MIDPOINT)
        _press(pointer, ON_THE_VIDEO)

        assert player.seeks == [DURATION_MS / 2]


class TestTheChip:
    def test_a_press_on_the_chip_sets_the_volume_and_does_not_seek(self, tmp_path):
        # The chip is composited over the scrubber, so it is asked first: a press
        # on it is never also a press on the row it sits in.
        pointer, player, _hud = _pointer(tmp_path)

        _press(pointer, CHIP_HALFWAY)

        assert player.volume == 50
        assert player.seeks == []

    def test_a_press_on_the_speaker_unmutes_and_does_not_seek(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path)

        _press(pointer, SPEAKER)

        assert player.muted is False
        assert player.seeks == []


class TestDragging:
    def test_a_held_pointer_on_the_track_keeps_setting_the_level(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path)

        _motion(pointer, CHIP_HALFWAY, held=True)

        assert player.volume == 50

    def test_an_unheld_pointer_sets_nothing(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path)

        _motion(pointer, CHIP_HALFWAY, held=False)

        assert player.volume == 100        # untouched, and still muted
        assert player.muted is True

    def test_the_hud_is_told_where_the_pointer_went_either_way(self, tmp_path):
        # It names the button under the cursor and draws the tooltip, which is a
        # question about hover rather than about who holds the drag.
        pointer, _player, hud = _pointer(tmp_path)

        _motion(pointer, ON_THE_VIDEO, held=False)
        _motion(pointer, CHIP_HALFWAY, held=True)

        assert hud.motions == [ON_THE_VIDEO, CHIP_HALFWAY]


class TestOrigeneratorMode:
    def test_a_suppressed_player_gives_the_whole_window_to_the_hud(self, tmp_path):
        # The region is the hosted app's: the scrubber and the chip come off the
        # video for the whole mode, and the HUD's mode row is the way back — so
        # nothing under them may take a press away from it.
        pointer, player, hud = _pointer(tmp_path, suppressed=True)

        _press(pointer, BAR_MIDPOINT)
        _press(pointer, CHIP_HALFWAY)

        assert player.seeks == []
        assert (player.volume, player.muted) == (100, True)
        assert hud.presses == [BAR_MIDPOINT, CHIP_HALFWAY]

    def test_a_suppressed_player_takes_no_drag_either(self, tmp_path):
        pointer, player, _hud = _pointer(tmp_path, suppressed=True)

        _motion(pointer, CHIP_HALFWAY, held=True)

        assert player.volume == 100
