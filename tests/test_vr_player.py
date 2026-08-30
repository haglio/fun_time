"""fun_time_vr.player off the headset: the manifest contract and the furniture.

The scene, the eyes and the OpenXR frame loop need the real machine and stay
with the VR integration run; these pin what runs the same everywhere — how the
player is told about its session, and the repaint economy of the scrubber and
volume chip every video unit paints.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from player_core.volume import VolumeHud, VolumeHudPainter

from fun_time_vr.player import _VideoUnit, _read_manifest, build_parser


def test_the_player_is_told_its_manifest_and_nothing_else():
    """One argument, required: everything the player needs — files, media,
    layout — arrives through the same manifest every other child reads."""
    args = build_parser().parse_args(["--manifest", "C:/state/windows_bridge_launch.ini"])
    assert str(args.manifest).replace("\\", "/") == "C:/state/windows_bridge_launch.ini"

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_the_manifest_reads_back_with_its_key_case_intact(tmp_path):
    """optionxform=str, like every other reader of this file: the keys are a
    cross-process contract and their spelling is load-bearing."""
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text("[commands]\nnau_cmd_file=C:/state/nau_cmd.txt\n", encoding="utf-8")

    manifest = _read_manifest(path)

    assert manifest["commands"]["nau_cmd_file"] == "C:/state/nau_cmd.txt"


class _OverlayPlayer:
    def __init__(self):
        self.overlays: list[tuple[int, int, int]] = []

    def overlay(self, ident, x, y, _bgra):
        self.overlays.append((ident, x, y))


def _unit_with_pixels(width=640, height=480) -> tuple[_VideoUnit, _OverlayPlayer]:
    player = _OverlayPlayer()
    unit = _VideoUnit.__new__(_VideoUnit)
    unit.player = player
    unit._scrubber_shown = None
    unit._chip_shown = None
    # A target that already holds pixels; the GL half is the integration
    # suite's, and overlay_furniture reads only these three fields of it.
    unit.target = SimpleNamespace(ready=True, width=width, height=height)
    return unit, player


def test_the_furniture_is_painted_once_and_not_per_tick():
    """The pump calls this every frame; the scrubber and chip must repaint
    only when what they SHOW moves, not sixty times a second."""
    unit, player = _unit_with_pixels()
    hud = VolumeHud(volume=80, muted=True)
    painter = VolumeHudPainter()

    unit.overlay_furniture(1_000.0, 600_000.0, hud, painter)
    assert len(player.overlays) == 2  # the scrubber and the chip, once each

    # A playhead move too small to cross a track pixel: byte-identical bar.
    unit.overlay_furniture(1_001.0, 600_000.0, hud, painter)
    assert len(player.overlays) == 2

    # A move that lands the cursor on another pixel repaints the scrubber
    # alone; the chip shows the same volume and stays.
    unit.overlay_furniture(300_000.0, 600_000.0, hud, painter)
    assert len(player.overlays) == 3


def test_no_furniture_lands_before_the_target_holds_pixels():
    unit, player = _unit_with_pixels()
    unit.target = SimpleNamespace(ready=False, width=0, height=0)

    unit.overlay_furniture(1_000.0, 600_000.0, VolumeHud(), VolumeHudPainter())

    assert player.overlays == []
