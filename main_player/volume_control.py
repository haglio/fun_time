"""The main player's volume chip: the level it shows, and what a press on it asks for.

The main player's sound is Fun Time's to decide — the main player's mpv is one of two sinks
it drives, Genau's clip audio being the other — so the level here is drawn and
reported, never set.  A press shows the new level at once *and* asks for it at
the same time: the authority's answer is a tick away, and a slider that waited
for it would trail the pointer by a frame.  The answer overwrites this one
either way, so an ignored press corrects itself rather than sticking.

The geometry is the shared chip's (:mod:`player_core.volume`), placed from the
window's lower-right corner and riding up with the timeline row beneath it.
This takes window coordinates and does that undoing itself, so nothing above it
holds a second idea of where the chip is.

Lived as two closures inside ``main_player.app``'s run loop over a ``nonlocal``.
"""
from __future__ import annotations

from dataclasses import replace

from player_core.volume import VolumeHud, chip_local, hit_part, volume_at


class VolumeControl:
    """The chip, and the channel a press on it goes out on."""

    def __init__(self, dashboard) -> None:
        self._dashboard = dashboard
        # Full and unmuted until Fun Time says otherwise: a chip opening at
        # silence would report a level the player is not at.
        self._hud = VolumeHud()

    @property
    def hud(self) -> VolumeHud:
        """What the chip currently shows — the level, and the mute over it."""
        return self._hud

    def set(self, level: int, muted: bool) -> None:
        """Take Fun Time's answer about the main slot's sound.

        The mute arrives as a fact of its own rather than as a level of zero:
        muted and turned-all-the-way-down look the same drawn, and unmuting has
        to come back to the level the speaker chose.
        """
        self._hud = VolumeHud(volume=level, muted=muted)

    def press_at(self, mx: int, my: int, *,
                 win_w: int, win_h: int, timeline_h: int) -> bool:
        """Take a press at window ``(mx, my)``; False if it missed the chip.

        A miss falls through to the video under it, where it seeks or pauses — the
        chip floats over the video, so a press on it is never also a press on
        what is under it.
        """
        return self._press(*chip_local(mx, my, win_w=win_w, win_h=win_h,
                                       timeline_h=timeline_h))

    def drag_at(self, mx: int, my: int, *,
                win_w: int, win_h: int, timeline_h: int) -> None:
        """Keep setting the level while the pointer is held down on the track.

        Only the track: the mute is a press, so a pointer crossing the speaker
        on its way to the slider must not flip it on the way past.  A drag that
        began elsewhere misses the chip and does nothing.
        """
        cx, cy = chip_local(mx, my, win_w=win_w, win_h=win_h, timeline_h=timeline_h)
        if hit_part(cx, cy) == "track":
            self._press(cx, cy)

    def _press(self, cx: int, cy: int) -> bool:
        part = hit_part(cx, cy)
        if part == "mute":
            self._hud = replace(self._hud, muted=not self._hud.muted)
            self._dashboard.post(
                "audio_unmute" if not self._hud.muted else "audio_mute")
        elif part == "track":
            level = volume_at(cx)
            self._hud = VolumeHud(volume=level, muted=False)
            self._dashboard.post(f"audio_set_volume|{level}")
        return bool(part)
