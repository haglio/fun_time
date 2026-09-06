"""fun_time_vr.player off the headset: the manifest contract and the furniture.

The scene, the eyes and the OpenXR frame loop need the real machine and stay
with the VR integration run; these pin what runs the same everywhere — how the
player is told about its session, and the repaint economy of the scrubber and
volume chip every video unit paints.
"""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

import numpy as np
import pytest
from player_core.console import ConsoleModel
from player_core.console_hud import ConsoleHud
from player_core.drive_readout import DriveHud
from player_core.volume import VolumeHud, VolumeHudPainter

from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    LaunchManifest,
    write_manifest_data,
)
from fun_time_vr.layout import DEFAULT_LAYOUT, LANDSCAPE, PANEL, PORTRAIT, read_layout
from fun_time_vr.player import (
    VrSettings,
    _HangingScreen,
    _LayoutKeeper,
    _MainUnit,
    _PanelUnit,
    _SatelliteUnit,
    _VideoUnit,
    build_parser,
)
from fun_time_vr.pointer import PRESS, RELEASE, SURFACE, Frame, Hover, PressEvent
from fun_time_vr.scene import Placement


def test_the_player_is_told_its_manifest_and_nothing_else():
    """One argument, required: everything the player needs — files, media,
    layout — arrives through the same manifest every other child reads."""
    args = build_parser().parse_args(["--manifest", "C:/state/windows_bridge_launch.ini"])
    assert str(args.manifest).replace("\\", "/") == "C:/state/windows_bridge_launch.ini"

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_the_vr_section_reads_back_with_its_key_case_intact(tmp_path):
    """optionxform=str, like every other reader of this file: the keys are a
    cross-process contract and their spelling is load-bearing.  The base
    sections are fun_time.manifest's; this section is FunTimeVR's own, and no
    desktop session ever writes it."""
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text(
        "[vr]\n"
        "tcode_udp_host=127.0.0.1\n"
        "tcode_udp_port=8000\n"
        "library_dirs=C:/vr/one|C:/vr/two\n"
        "audio_device=Example Headset\n"
        "compositor_layers=1\n",
        encoding="utf-8")

    vr = VrSettings.read(path)

    assert vr.tcode_udp_host == "127.0.0.1"
    assert vr.tcode_udp_port == 8000
    assert [str(p).replace("\\", "/") for p in vr.library_dirs] == ["C:/vr/one", "C:/vr/two"]
    assert vr.audio_device == "Example Headset"
    assert vr.compositor_layers is True


def test_the_compositor_falls_back_to_off_when_the_section_does_not_say(tmp_path):
    """The one key here with a fallback: a manifest written before layers
    existed must not turn them on."""
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text(
        "[vr]\ntcode_udp_host=127.0.0.1\ntcode_udp_port=8000\nlibrary_dirs=\n",
        encoding="utf-8")

    assert VrSettings.read(path).compositor_layers is False


def test_a_session_that_names_no_audio_device_reads_back_as_none_named(tmp_path):
    """The main player asks mpv for a device only when the session named one,
    so an unnamed device has to arrive as the empty string rather than absent."""
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text(
        "[vr]\ntcode_udp_host=127.0.0.1\ntcode_udp_port=8000\nlibrary_dirs=\n",
        encoding="utf-8")

    assert VrSettings.read(path).audio_device == ""


# --- The two units' construction: every file they need, out of the manifest ---

# The collaborators a unit builds that need libmpv, a GL context, a socket or
# the state directory.  Faked wholesale: what is under test here is which
# manifest field each path comes from, not what is done with it afterwards.
_UNIT_COLLABORATORS = (
    "MpvRenderPlayer", "RenderTarget", "FrameTexture", "MainRole", "SatelliteSession",
    "StatusWriter", "HudOverlay", "FunscriptTCodeDriver", "UdpTCodeSink",
    "VolumeHudPainter", "DriveGate",
)


def _manifest_for_a_vr_session(tmp_path) -> LaunchManifest:
    """A real manifest, written by the writer the VR launcher uses."""
    from fun_time.config import load_config  # noqa: PLC0415
    from fun_time_vr.orchestrator import build_vr_manifest  # noqa: PLC0415

    config = load_config(Path("fun_time_config.example.json"))
    path = write_manifest_data(
        build_vr_manifest(config), tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    return LaunchManifest.read(path)


@pytest.fixture
def faked_collaborators():
    """Every unit collaborator recorded rather than built."""
    with patch.multiple(
        "fun_time_vr.player", **dict.fromkeys(_UNIT_COLLABORATORS, DEFAULT)
    ) as fakes, \
            patch("fun_time_vr.player.read_paused_state", return_value=False), \
            patch("fun_time_vr.player.read_playlist", return_value=[]):
        yield fakes


def test_the_main_unit_finds_every_file_it_needs_in_the_manifest(
        tmp_path, faked_collaborators):
    """The primary reads four paths and one device name out of the session it
    was handed; a spelling that no longer resolves raises here rather than on
    the headset, where the unit is built with no console to say so."""
    manifest = _manifest_for_a_vr_session(tmp_path)
    vr = VrSettings(
        tcode_udp_host="127.0.0.1", tcode_udp_port=8000, library_dirs=(),
        audio_device="Example Headset", compositor_layers=False,
    )

    unit = _MainUnit(manifest, vr, lambda _name: 0)

    commands = manifest.commands
    assert unit.cmd_file == Path(commands.nau_cmd_file)
    assert unit.paused_file == Path(commands.nau_paused_file)
    assert faked_collaborators["StatusWriter"].call_args.args[0] == Path(
        commands.nau_status_file)
    assert faked_collaborators["MainRole"].call_args.kwargs["playlist_file"] == Path(
        commands.nau_playlist_file)
    # The one that is not a path, and the one that had no field to land in at
    # all until this branch: without it `route_audio` never asks mpv for the
    # headset's sink, and the primary's sound stays on the room speakers.
    assert unit._audio_device == "Example Headset"


@pytest.mark.parametrize("side", ["portrait", "landscape"])
def test_a_satellite_unit_finds_every_file_it_needs_in_the_manifest(
        side, tmp_path, faked_collaborators):
    """Six paths per side, five of them asked for by side rather than spelled
    out — and the sixth, the dashboard's command file, shared with the desktop."""
    manifest = _manifest_for_a_vr_session(tmp_path)

    unit = _SatelliteUnit(side, manifest, lambda _name: 0, placement=DEFAULT_LAYOUT[side])

    commands = manifest.commands
    assert unit.cmd_file == Path(commands.side_file(side, "cmd"))
    assert unit.paused_file == Path(commands.side_file(side, "paused"))
    assert unit.playlist_file == Path(commands.side_file(side, "playlist"))
    assert faked_collaborators["StatusWriter"].call_args.args[0] == Path(
        commands.side_file(side, "status"))
    hud = faked_collaborators["HudOverlay"].call_args.kwargs
    assert hud["hud_file"] == Path(commands.side_file(side, "hud"))
    assert hud["command_file"] == Path(commands.dashboard_cmd_file)
    # The HUD paints into a surface of its own, hanging under the picture, not
    # into the video through mpv as the desktop satellite's does.
    assert hud["player"] is unit.hud_surface


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


class TestWhatEveryVideoUnitOwes:
    """The worker calls ``pump(stop, now)`` on everything in its list, and the
    list is annotated ``list[_VideoUnit]`` — which promised a ``player`` it
    closes and said nothing about the one method that has to be there."""

    def test_the_base_refuses_to_be_pumped(self):
        unit = _VideoUnit.__new__(_VideoUnit)

        with pytest.raises(NotImplementedError):
            unit.pump(threading.Event(), 0.0)

    def test_the_base_refuses_to_be_closed(self):
        """The frame loop's `finally` calls this on every unit in its list, so
        a subclass that forgot would raise inside the teardown and leave the
        OpenXR session and the GL contexts unreleased."""
        unit = _VideoUnit.__new__(_VideoUnit)

        with pytest.raises(NotImplementedError):
            unit.close()

    @pytest.mark.parametrize("unit_class", [_MainUnit, _SatelliteUnit])
    def test_both_units_answer_the_calls_the_loop_makes(self, unit_class):
        """By convention until now: signatures that happened to match."""
        import inspect

        assert unit_class.pump is not _VideoUnit.pump
        assert unit_class.close is not _VideoUnit.close
        assert list(inspect.signature(unit_class.pump).parameters) == [
            "self", "stop", "now"]


# --- The screens the controllers can move ---------------------------------


class _FakeMesh:
    def __init__(self):
        self.uploads = []

    def upload(self, vertices):
        self.uploads.append(vertices)


def test_a_screen_rehangs_when_its_placement_moves_and_only_then():
    screen = _HangingScreen(DEFAULT_LAYOUT[LANDSCAPE])
    screen.mesh = _FakeMesh()

    screen.rehang(4 / 3)
    screen.rehang(4 / 3)
    assert len(screen.mesh.uploads) == 1

    screen.placement = Placement(azimuth_deg=50.0, elevation_deg=0.0, width_deg=40.0)
    screen.rehang(4 / 3)
    screen.rehang(4 / 3)

    assert len(screen.mesh.uploads) == 2
    assert not np.array_equal(screen.mesh.uploads[0], screen.mesh.uploads[1])


def test_a_satellite_hangs_where_the_layout_says(tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path)
    moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

    unit = _SatelliteUnit("portrait", manifest, lambda _name: 0, placement=moved)

    assert unit.screen.placement == moved


class TestTheLayoutKeeper:
    def test_what_the_controllers_settled_is_written_once_on_the_worker(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, dict(DEFAULT_LAYOUT))
        moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

        keeper.pump(threading.Event(), 0.0)
        assert not path.exists()

        keeper.place(PORTRAIT, moved)
        assert not path.exists()  # a screen mid-drag is not worth a file yet

        keeper.settle()
        keeper.pump(threading.Event(), 0.0)
        assert read_layout(path)[PORTRAIT] == moved

        written = path.stat().st_mtime_ns
        keeper.pump(threading.Event(), 0.0)
        assert path.stat().st_mtime_ns == written

    def test_a_session_ending_mid_drag_still_keeps_the_screen_where_it_was_left(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, dict(DEFAULT_LAYOUT))
        moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

        keeper.place(PORTRAIT, moved)
        keeper.close()

        assert read_layout(path)[PORTRAIT] == moved


class TestThePanelUnderThePointer:
    def _unit(self, tmp_path):
        seeks: list[float] = []
        primary = SimpleNamespace(
            role=SimpleNamespace(
                current_video=Path("feature.mp4"), position_ms=1_000.0, duration_ms=10_000.0,
                volume=70, muted=False, seek_to=seeks.append,
            ),
            drive_gate=SimpleNamespace(readout=lambda published: published),
        )
        genau = SimpleNamespace(role=SimpleNamespace(
            console_hud=ConsoleHud(
                console=ConsoleModel(mode="video", broker=True, locked=False),
                drive=DriveHud(speed=50, amplitude=60, center=50, shape="sine", position=1000,
                               advance_interval=10, waveform=tuple([0.5] * 80),
                               trace_seconds=12.0),
            ),
            current_clip=None, loading=None, showing=False, volume=100, muted=False,
        ))
        command_file = tmp_path / "dashboard_cmd.txt"
        with patch("fun_time_vr.player.FrameTexture"):
            unit = _PanelUnit(primary, genau, placement=DEFAULT_LAYOUT[PANEL],
                              dashboard_cmd_file=command_file)
        return SimpleNamespace(unit=unit, command_file=command_file, seeks=seeks)

    def _uv_of(self, unit, action: str) -> tuple[float, float]:
        width, height = unit._image.size
        (x, y, w, h), _button = next(
            (rect, button) for rect, button in unit._painter.buttons if button.action == action)
        return (x + w // 2 + 0.5) / width, 1 - (y + h // 2 + 0.5) / height

    def test_a_press_the_render_thread_hands_over_posts_on_the_worker(self, tmp_path):
        p = self._unit(tmp_path)
        p.unit.pump(threading.Event(), 0.0)  # painted: the buttons now have places

        p.unit.point(Frame(events=(
            PressEvent(PRESS, PANEL, *self._uv_of(p.unit, "main_lock")), PressEvent(RELEASE, PANEL),
        )))
        assert not p.command_file.exists()

        p.unit.pump(threading.Event(), 0.0)

        assert p.command_file.read_text(encoding="utf-8").split() == ["main_lock"]

    def test_hovering_a_button_names_it_on_the_panel(self, tmp_path):
        p = self._unit(tmp_path)
        p.unit.pump(threading.Event(), 0.0)
        plain = np.asarray(p.unit._image).copy()

        p.unit.point(Frame(hover=Hover(PANEL, SURFACE, *self._uv_of(p.unit, "main_lock"))))
        p.unit.pump(threading.Event(), 0.0)

        assert not np.array_equal(np.asarray(p.unit._image), plain)
