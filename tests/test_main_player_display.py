from __future__ import annotations

import numpy as np

from main_player.display import Display, black_bgra


class SpyPlayer:
    """mpv's overlay surface, recorded: what is up, and every call in order."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.up: dict[int, np.ndarray] = {}

    def overlay(self, ident: int, x: int, y: int, bgra) -> None:
        self.calls.append(("overlay", ident, x, y, bgra.shape))
        self.up[ident] = bgra

    def remove_overlay(self, ident: int) -> None:
        self.calls.append(("remove", ident))
        self.up.pop(ident, None)


HUD_IDS = (0, 4, 6)


def _display() -> tuple[Display, SpyPlayer]:
    player = SpyPlayer()
    return Display(player, HUD_IDS), player


class TestBlackBgra:
    def test_is_opaque_black_at_the_asked_size(self):
        frame = black_bgra(320, 240)

        assert frame.shape == (240, 320, 4)
        assert frame.dtype == np.uint8
        assert not frame[:, :, :3].any(), "every color channel is black"
        assert (frame[:, :, 3] == 255).all(), "opaque, or the video shows through"

    def test_a_degenerate_size_still_makes_a_block(self):
        # A window mid-resize can report zero; an empty array would be rejected
        # by the overlay call rather than merely covering nothing.
        assert black_bgra(0, 0).shape == (1, 1, 4)


class TestDisplay:
    def test_starts_on_so_a_standalone_run_paints(self):
        """Nothing tells a bare `python -m main_player` anything about a main slot,
        so the default has to be the one that shows the video."""
        display, player = _display()

        display.sync(800, 600)

        assert display.active
        assert player.calls == []

    def test_off_covers_the_window_and_takes_the_hud_down(self):
        display, player = _display()

        display.set_active(False)
        display.sync(800, 600)

        assert player.calls[:len(HUD_IDS)] == [("remove", ident) for ident in HUD_IDS]
        assert list(player.up) == [8], "only the black is left up"
        assert player.up[8].shape == (600, 800, 4)

    def test_the_black_covers_the_whole_window(self):
        display, player = _display()

        display.set_active(False)
        display.sync(1280, 720)

        _call, _ident, x, y, shape = player.calls[-1]
        assert (x, y) == (0, 0)
        assert shape == (720, 1280, 4)

    def test_staying_off_repaints_nothing(self):
        """An mpv overlay stays up until it is removed or replaced, so the
        frames after the first cost nothing."""
        display, player = _display()
        display.set_active(False)
        display.sync(800, 600)
        player.calls.clear()

        for _ in range(3):
            display.sync(800, 600)

        assert player.calls == []

    def test_a_resize_while_off_repaints_the_black_to_fit(self):
        display, player = _display()
        display.set_active(False)
        display.sync(800, 600)
        player.calls.clear()

        display.sync(1024, 768)

        assert player.up[8].shape == (768, 1024, 4)

    def test_coming_back_on_takes_the_black_down(self):
        display, player = _display()
        display.set_active(False)
        display.sync(800, 600)
        player.calls.clear()

        display.set_active(True)
        display.sync(800, 600)

        assert player.calls == [("remove", 8)]
        assert player.up == {}, "the HUD paints itself again from the next frame"

    def test_staying_on_removes_nothing(self):
        """The remove would land on whatever id the HUD had just drawn with."""
        display, player = _display()

        for _ in range(3):
            display.sync(800, 600)

        assert player.calls == []
