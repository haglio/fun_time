"""What the mouse does to the main player's window.

Four things are under the pointer, each floating over the one under it: the
console, the volume chip at the right-hand end of the timeline row,
the rest of that row, and the video everywhere else.  800x600 with no heatmap
built puts the row's top edge at y=576 and the inset track between x=40 and
x=668.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from player_core.funscript import Funscript

from main_player.dashboard import Dashboard
from main_player.overlay import HeatmapStrip
from main_player.pointer import OMNIPAUSE_TOGGLE, Pointer
from main_player.volume_control import VolumeControl

DURATION_MS = 100_000.0
ON_THE_VIDEO = (400, 300)
TRACK_START = (40, 590)
TRACK_MIDDLE = (354, 590)
PAST_THE_TRACKS_END = (790, 590)
ON_THE_VOLUME_CHIP = (744, 590)


class SpySession:
    """The player, as a press reaches it: where it was sent."""

    def __init__(self, duration_ms: float = DURATION_MS) -> None:
        self.duration_ms = duration_ms
        self.seeks: list[float] = []

    def seek_to(self, position_ms: float) -> None:
        self.seeks.append(position_ms)


class SpyConsole:
    """The console, as the pointer meets them.

    The real one is player_core's; what a press has to get right here is only
    whether the console took it, whether the press landed on the console at all,
    and which control a held pointer belongs to.  *asks* is what a press on a
    button posts ("" for a press that hit no button), *drags* what the pointer
    posts while a band is held, and *drawn_at* the rectangle the panel occupies.
    """

    def __init__(self, *, asks: str = "", drags: str = "",
                 drawn_at: tuple[int, int, int, int] | None = None) -> None:
        self._asks = asks
        self._drags = drags
        self._drawn_at = drawn_at
        self.holding = False
        self.releases = 0
        self.hovered: tuple[int, int] | None = None

    def covers(self, mx: int, my: int) -> bool:
        if self._drawn_at is None:
            return False
        x, y, w, h = self._drawn_at
        return x <= mx < x + w and y <= my < y + h

    def press_at(self, mx: int, my: int) -> str:
        if not self._asks:
            return ""
        self.holding = True
        return self._asks

    def drag_to(self, mx: int, my: int) -> str:
        return self._drags

    def release(self) -> None:
        self.holding = False
        self.releases += 1

    def hover_at(self, mx: int, my: int) -> tuple[int, int] | None:
        return self.hovered


class Bits:
    """A pointer, and everything a press could reach through it."""

    def __init__(self, tmp_path: Path, console: SpyConsole | None = None) -> None:
        self.cmd_file = tmp_path / "dashboard_cmd.txt"
        dashboard = Dashboard(self.cmd_file)
        self.session = SpySession()
        self.heatmap = HeatmapStrip()
        self.volume = VolumeControl(dashboard)
        self.console = console if console is not None else SpyConsole()
        self.pointer = Pointer(self.session, self.heatmap, self.volume,
                               self.console, dashboard)

    def press(self, at: tuple[int, int], *, win_w: int = 800, win_h: int = 600) -> None:
        self.pointer.press(*at, win_w=win_w, win_h=win_h)

    def motion(self, at: tuple[int, int], *, held: bool,
               win_w: int = 800, win_h: int = 600) -> None:
        self.pointer.motion(*at, held=held, win_w=win_w, win_h=win_h)

    def asks(self) -> list[str]:
        """What the pointer has asked Fun Time for, in order."""
        if not self.cmd_file.exists():
            return []
        return self.cmd_file.read_text(encoding="utf-8").split()


def _funscript() -> Funscript:
    return Funscript(actions=[(0, 0), (1000, 100), (2000, 0)])


def _recording_strip(bits: Bits) -> None:
    """Grow the strip the way a loop being recorded does: taller, and zoomed
    into the section around the in point."""
    bits.heatmap.update("v0.mp4", _funscript(), DURATION_MS, width=628,
                        loop_state="recording", record_in_ms=1000.0, position_ms=1200.0)


@pytest.fixture
def bits(tmp_path: Path) -> Bits:
    return Bits(tmp_path)


class TestPressingTheVideo:
    """In a session this player's paused state is the room's flag file, re-read
    every pass, so it has no pause of its own to give: a press on the video asks
    Fun Time to freeze the whole room, and the next one asks it off again."""

    def test_it_asks_the_room_to_pause(self, bits):
        bits.press(ON_THE_VIDEO)

        assert bits.asks() == [OMNIPAUSE_TOGGLE]
        assert bits.session.seeks == []

    def test_a_press_just_above_the_timeline_row_is_still_the_video(self, bits):
        """The row is 24px tall with no strip built, so 575 is the last row of
        video and 576 the first of the timeline."""
        bits.press((400, 575))

        assert bits.asks() == [OMNIPAUSE_TOGGLE]

    def test_a_player_with_no_session_to_ask_does_nothing(self, tmp_path):
        """Launched by hand rather than by Fun Time: there is no room to
        freeze, and the press is still not an error."""
        bits = Bits(tmp_path)
        bits.pointer = Pointer(bits.session, bits.heatmap, bits.volume,
                               bits.console, Dashboard(None))

        bits.press(ON_THE_VIDEO)

        assert bits.asks() == []


class TestPressingTheTimeline:
    def test_it_seeks_and_does_not_touch_the_pause(self, bits):
        bits.press(TRACK_MIDDLE)

        assert bits.session.seeks == [pytest.approx(DURATION_MS / 2)]
        assert bits.asks() == []

    def test_the_start_of_the_track_is_the_start_of_the_video(self, bits):
        bits.press(TRACK_START)

        assert bits.session.seeks == [pytest.approx(0.0)]

    def test_the_track_is_inset_so_the_left_edge_is_still_the_start(self, bits):
        """The track starts a margin in from the window edge; a press in that
        margin saturates rather than seeking to a negative time."""
        bits.press((2, 590))

        assert bits.session.seeks == [pytest.approx(0.0)]

    def test_past_the_end_of_the_track_is_the_end_of_the_video(self, bits):
        """The track stops clear of the volume chip, so the pixels between the
        two are past the end rather than off the map."""
        bits.press(PAST_THE_TRACKS_END)

        assert bits.session.seeks == [pytest.approx(DURATION_MS)]

    def test_a_taller_strip_makes_the_row_reach_further_up_the_window(self, bits):
        """A loop being recorded grows the strip to 48px, so a press at 560 --
        video a moment ago -- is a seek now."""
        _recording_strip(bits)

        bits.press((354, 560))

        assert bits.asks() == []
        assert len(bits.session.seeks) == 1

    def test_it_seeks_inside_the_window_the_strip_is_showing(self, bits):
        """While recording, the strip is zoomed into the section around the in
        point, and the track under it maps that window rather than the whole
        video -- otherwise the picture and the press disagree."""
        _recording_strip(bits)
        start_ms, end_ms = bits.heatmap.window

        bits.press(TRACK_MIDDLE)

        assert end_ms - start_ms < DURATION_MS, "the strip is not zoomed; case proves nothing"
        assert start_ms <= bits.session.seeks[0] <= end_ms
        assert bits.session.seeks[0] != pytest.approx(DURATION_MS / 2)

    def test_before_any_strip_is_built_the_video_own_length_is_the_map(self, bits):
        """The strip reports an empty window until its first build, and a press
        in that first frame still has to land somewhere sensible."""
        assert bits.heatmap.window == (0.0, 0.0)

        bits.press(TRACK_MIDDLE)

        assert bits.session.seeks == [pytest.approx(DURATION_MS / 2)]


class TestPressingTheVolumeChip:
    def test_the_chip_takes_the_press_before_the_video_behind_it(self, bits):
        """It floats over the video, so a press on it is never also a press on
        what is under it."""
        bits.press(ON_THE_VOLUME_CHIP)

        assert bits.volume.hud.volume == 50
        assert bits.session.seeks == []
        assert bits.asks() == ["audio_set_volume|50"]  # the chip's own, not the room's


class TestPressingAConsoleButton:
    def test_the_console_takes_the_press_and_posts_its_verb(self, tmp_path):
        """The verbs are the room's, not this player's, so a button asks Fun
        Time rather than acting."""
        bits = Bits(tmp_path, SpyConsole(asks="main_next"))

        bits.press(ON_THE_VIDEO)

        assert bits.asks() == ["main_next"]

    def test_a_press_the_console_took_never_reaches_the_video(self, tmp_path):
        bits = Bits(tmp_path, SpyConsole(asks="main_next"))

        bits.press(ON_THE_VIDEO)

        assert (bits.asks(), bits.session.seeks) == (["main_next"], [])

    def test_a_press_the_console_took_never_reaches_the_chip(self, tmp_path):
        """The console is drawn over the top-left corner and the chip sits at
        the lower right, but the order is what makes that a fact rather than a
        coincidence of where they happen to be."""
        bits = Bits(tmp_path, SpyConsole(asks="main_next"))

        bits.press(ON_THE_VOLUME_CHIP)

        assert bits.volume.hud.volume == 100

    def test_a_press_on_the_console_between_its_buttons_asks_for_nothing(self, tmp_path):
        """The console dims three of its controls for most videos, and a press that
        lands on the panel but on no live button used to fall through and pause the
        room; a press anywhere the panel covers now stops at it."""
        bits = Bits(tmp_path, SpyConsole(drawn_at=(8, 8, 300, 200)))

        bits.press((150, 100))

        assert (bits.asks(), bits.session.seeks) == ([], [])

    def test_a_press_off_the_console_falls_through(self, bits):
        bits.press(ON_THE_VIDEO)

        assert bits.asks() == [OMNIPAUSE_TOGGLE]


class TestDragging:
    def test_a_drag_reaches_the_volume_slider(self, bits):
        bits.motion(ON_THE_VOLUME_CHIP, held=True)

        assert bits.volume.hud.volume == 50

    def test_a_drag_over_the_video_moves_nothing(self, bits):
        bits.motion(ON_THE_VIDEO, held=True)

        assert bits.volume.hud.volume == 100
        assert (bits.session.seeks, bits.asks()) == ([], [])

    def test_a_held_console_band_keeps_the_drag_even_off_itself(self, tmp_path):
        """The band a press took hold of keeps the pointer as it wanders --
        including out over the volume chip, which must not answer it."""
        bits = Bits(tmp_path, SpyConsole(asks="osr2_amp|40", drags="osr2_amp|55"))
        bits.press(ON_THE_VIDEO)

        bits.motion(ON_THE_VOLUME_CHIP, held=True)

        assert bits.asks() == ["osr2_amp|40", "osr2_amp|55"]
        assert bits.volume.hud.volume == 100

    def test_a_held_band_says_nothing_while_its_level_has_not_moved(self, tmp_path):
        """A drag fires per mouse motion, and every one that says nothing new
        would be a line in the command file for Fun Time to route to a value
        Genau is already on."""
        bits = Bits(tmp_path, SpyConsole(asks="osr2_amp|40", drags=""))
        bits.press(ON_THE_VIDEO)

        bits.motion(ON_THE_VOLUME_CHIP, held=True)

        assert bits.asks() == ["osr2_amp|40"]

    def test_a_pointer_that_is_not_held_lets_go_of_whatever_was(self, tmp_path):
        """The button came up somewhere this loop never saw it -- over another
        window, or off the screen -- so nothing is held any more."""
        bits = Bits(tmp_path, SpyConsole(asks="osr2_amp|40", drags="osr2_amp|55"))
        bits.press(ON_THE_VIDEO)

        bits.motion(ON_THE_VIDEO, held=False)

        assert bits.console.holding is False
        assert bits.asks() == ["osr2_amp|40"]

    def test_letting_go_releases_the_console(self, bits):
        bits.pointer.release()

        assert bits.console.releases == 1


class TestNamingTheButtonUnderThePointer:
    def test_the_pointer_carries_where_to_name_it(self, tmp_path):
        console = SpyConsole()
        console.hovered = (12, 34)
        bits = Bits(tmp_path, console)

        bits.motion(ON_THE_VIDEO, held=False)

        assert bits.pointer.hover == (12, 34)

    def test_nothing_is_named_over_no_button(self, tmp_path):
        bits = Bits(tmp_path, SpyConsole())
        bits.motion(ON_THE_VIDEO, held=False)

        assert bits.pointer.hover is None
