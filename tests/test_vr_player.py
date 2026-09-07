"""fun_time_vr.player off the headset: the manifest contract and the furniture.

The scene, the eyes and the OpenXR frame loop need the real machine and stay
with the VR integration run; these pin what runs the same everywhere — how the
player is told about its session, and the repaint economy of the scrubber and
volume chip every video unit paints.
"""
from __future__ import annotations

import json
import logging
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
from fun_time.overlay_progress import (
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    SHUTDOWN_READY_FILENAME,
    PhaseProgress,
)
from fun_time_vr.console_panel import NOTICE_STRIP_HEIGHT, PANEL_WIDTH_DEG
from fun_time_vr.cover import VR_SHUTDOWN_PHASES, VR_STARTUP_PHASES
from fun_time_vr.furniture import control_size
from fun_time_vr.layout import (
    DEFAULT_LAYOUT,
    LANDSCAPE,
    PANEL,
    PORTRAIT,
    PRIMARY,
    read_layout,
)
from fun_time_vr.notices import NoticeBoard
from fun_time_vr.player import (
    VrSettings,
    _CoverUnit,
    _draw_eyes,
    _GenauUnit,
    _HangingScreen,
    _LayoutKeeper,
    _main_slot_screen,
    _MainUnit,
    _PanelUnit,
    _pointable_screens,
    _SatelliteUnit,
    _scene_is_up,
    _VideoUnit,
    build_parser,
)
from fun_time_vr.pointer import PRESS, RELEASE, SURFACE, Frame, Hover, PressEvent
from fun_time_vr.projection import EQUIRECT_180_SBS, FLAT
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

    unit = _MainUnit(manifest, vr, lambda _name: 0, placement=DEFAULT_LAYOUT[PRIMARY])

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

    vr = VrSettings(
        tcode_udp_host="127.0.0.1", tcode_udp_port=8000, library_dirs=(),
        audio_device="", compositor_layers=False,
    )

    unit = _SatelliteUnit(
        side, manifest, lambda _name: 0, vr=vr, placement=DEFAULT_LAYOUT[side])

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
    unit.target = SimpleNamespace(ready=True, width=width, height=height,
                                 aspect=width / height)
    unit.screen = SimpleNamespace(placement=DEFAULT_LAYOUT[PRIMARY])
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

    @property
    def ready(self):
        return bool(self.uploads)

    def close(self):
        pass


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

    vr = VrSettings(
        tcode_udp_host="127.0.0.1", tcode_udp_port=8000, library_dirs=(),
        audio_device="", compositor_layers=False,
    )

    unit = _SatelliteUnit("portrait", manifest, lambda _name: 0, vr=vr, placement=moved)

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


class _FakePanelTexture:
    """A FrameTexture with real numbers where the panel does arithmetic on them."""

    ready = True
    aspect = 280 / 120

    def upload(self, pixels):
        self.uploaded = pixels

    def close(self):
        pass


class TestThePanelUnderThePointer:
    def _unit(self, tmp_path):
        seeks: list[float] = []
        primary = SimpleNamespace(
            role=SimpleNamespace(
                current_video=Path("feature.mp4"), position_ms=1_000.0, duration_ms=10_000.0,
                volume=70, muted=False, seek_to=seeks.append, f_mode=False,
                speed=1.25,
            ),
            drive_gate=SimpleNamespace(readout=lambda published: published),
            target=SimpleNamespace(ready=True, aspect=16 / 9),
            screen=SimpleNamespace(placement=DEFAULT_LAYOUT[PRIMARY]),
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
        event_log = tmp_path / "event_log.jsonl"
        notices = NoticeBoard(event_log)
        with patch("fun_time_vr.player.FrameTexture", _FakePanelTexture):
            unit = _PanelUnit(primary, genau, dashboard_cmd_file=command_file,
                              notices=notices)
        return SimpleNamespace(unit=unit, command_file=command_file, seeks=seeks,
                               event_log=event_log, notices=notices, primary=primary)

    def _uv_of(self, unit, action: str) -> tuple[float, float]:
        """A button's middle in the PANEL's pixels: the painter places its buttons
        in the console's, which the announcement strip above pushes down."""
        width, height = unit._image.size
        (x, y, w, h), _button = next(
            (rect, button) for rect, button in unit._painter.buttons if button.action == action)
        y += NOTICE_STRIP_HEIGHT
        return (x + w // 2 + 0.5) / width, 1 - (y + h // 2 + 0.5) / height

    def test_a_notice_the_session_raised_reaches_the_panel(self, tmp_path):
        """A VR session launches no dashboard, so this strip is the whole of what
        the headset is told — the voice controller's reports among it."""
        p = self._unit(tmp_path)
        p.notices.pump(None, 0.0)
        p.unit.pump(threading.Event(), 0.0)
        quiet = np.asarray(p.unit._image).copy()

        p.event_log.write_text(json.dumps(
            {"ts": 1.0, "level": logging.ERROR, "source": "system",
             "msg": "unrecognized voice command: portrait net"}) + "\n", encoding="utf-8")
        p.notices.pump(None, 1.0)
        p.unit.pump(threading.Event(), 1.0)

        assert not np.array_equal(np.asarray(p.unit._image), quiet)

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

    def test_the_console_rides_under_the_main_player_wherever_it_goes(self, tmp_path):
        """Docked the way a satellite's HUD is docked, rather than placed: and
        re-placed every frame, not only the ones that repaint it, so it cannot lag
        the player it belongs to -- which is what left its old handle dragging
        an empty rectangle around for seconds at a time."""
        p = self._unit(tmp_path)
        p.unit.pump(threading.Event(), 0.0)
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            p.unit.render_latest_frame()
            docked = p.unit.screen.placement

            p.primary.screen.placement = Placement(
                azimuth_deg=-40.0, elevation_deg=12.0, width_deg=200.0)
            p.unit.render_latest_frame()
            followed = p.unit.screen.placement

        assert docked.azimuth_deg == DEFAULT_LAYOUT[PRIMARY].azimuth_deg
        assert docked.elevation_deg < 0.0  # under the picture, never over it
        assert followed.azimuth_deg == -40.0
        assert followed.elevation_deg < docked.elevation_deg  # a bigger player hangs lower
        assert followed.width_deg == docked.width_deg == PANEL_WIDTH_DEG


# --- The cover the roles arrive and leave under ---------------------------


class _FakeTexture:
    """A GL texture stand-in: the two numbers a hanging screen reads off one."""

    def __init__(self):
        self.uploads = []
        self.texture = 7

    def upload(self, pixels):
        self.uploads.append(pixels)

    @property
    def ready(self):
        return bool(self.uploads)

    aspect = 16 / 10

    def close(self):
        pass


@pytest.fixture
def cover_graphics():
    """The cover's texture and mesh, recorded rather than made — everything else
    about it is files and Pillow, which run anywhere."""
    with patch("fun_time_vr.player.FrameTexture", _FakeTexture),             patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
        yield


class TestTheCoverUnit:
    def test_an_idle_state_dir_shows_the_scene(self, tmp_path, cover_graphics):
        unit = _CoverUnit(tmp_path)

        unit.render_latest_frame()

        assert not unit.showing
        assert not unit.closing

    def test_a_launch_in_progress_is_covered_from_the_first_frame(
            self, tmp_path, cover_graphics):
        """Painted on the way in rather than on the worker's first turn: the
        loop's very first renderable frame is already one the cover has to be
        on, and a tick of scene before it is a tick of the room in the open."""
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("players")

        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()

        assert unit.showing
        assert len(unit.texture.uploads) == 1

    def test_the_bitmap_is_uploaded_once_per_change_not_per_frame(
            self, tmp_path, cover_graphics):
        progress = PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=VR_STARTUP_PHASES)
        progress.advance("players")
        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()
        unit.render_latest_frame()
        assert len(unit.texture.uploads) == 1

        progress.advance("finalizing")
        unit.pump(threading.Event(), 0.0)
        unit.render_latest_frame()

        assert len(unit.texture.uploads) == 2

    def test_done_hands_the_headset_back(self, tmp_path, cover_graphics):
        progress = PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=VR_STARTUP_PHASES)
        progress.advance("finalizing")
        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()
        assert unit.showing

        progress.finish()
        unit.pump(threading.Event(), 0.0)
        unit.render_latest_frame()

        assert not unit.showing

    def test_the_ready_flag_waits_for_a_closing_cover(self, tmp_path, cover_graphics):
        """Teardown holds its first kill for this flag, so a loading cover
        dropping it would hand back a promise about the wrong panel."""
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("players")
        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()

        unit.settled()

        assert not (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_a_painted_closing_cover_reports_itself(self, tmp_path, cover_graphics):
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("controls")
        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()
        assert unit.closing

        unit.settled()

        assert (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_a_closing_cover_answers_teardown_before_a_frame_is_drawn(
            self, tmp_path, cover_graphics):
        """The frame loop may never get another frame — the runtime may have
        ended the session already — and teardown is holding its first kill on
        this.  Painted is enough; drawn is a bonus."""
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("controls")

        unit = _CoverUnit(tmp_path)

        assert unit.closing
        assert not unit.showing  # nothing uploaded yet
        unit.settled()
        assert (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_an_idle_state_dir_wants_no_cover_at_all(self, tmp_path, cover_graphics):
        """A player run with no orchestrator -- the integration suite -- must
        not be held at bring-up waiting for a cover that is not coming."""
        assert not _CoverUnit(tmp_path).wanted

    def test_a_launch_in_progress_wants_one(self, tmp_path, cover_graphics):
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("players")

        assert _CoverUnit(tmp_path).wanted

    def test_the_anchor_is_let_go_when_the_cover_does(self, tmp_path, cover_graphics):
        """So the closing cover hangs where the viewer is by then, not where
        the loading one was, a whole session earlier."""
        progress = PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=VR_STARTUP_PHASES)
        progress.advance("players")
        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()
        assert unit.anchor.heading(1.2) == pytest.approx(1.2)

        progress.finish()
        unit.pump(threading.Event(), 0.0)
        unit.render_latest_frame()

        assert unit.anchor.heading(2.9) == pytest.approx(2.9)

    def test_the_player_ending_on_its_own_raises_its_own_closing_cover(
            self, tmp_path, cover_graphics):
        """Its window was closed, so nobody is going to write a shutdown file —
        and its own units are about to go down one at a time."""
        unit = _CoverUnit(tmp_path)
        unit.render_latest_frame()
        assert not unit.showing

        unit.closing_now()
        unit.refresh()
        unit.render_latest_frame()

        assert unit.showing
        assert unit.closing


# --- What the cover is held for -------------------------------------------


def _picture(ready):
    return SimpleNamespace(ready=ready)


def _room(*, main=True, portrait=True, landscape=True, panel=True, genau_showing=False):
    return dict(
        primary=SimpleNamespace(target=_picture(main)),
        genau=SimpleNamespace(texture=_picture(main),
                              role=SimpleNamespace(showing=genau_showing)),
        satellites=[SimpleNamespace(target=_picture(portrait)),
                    SimpleNamespace(target=_picture(landscape))],
        panel=SimpleNamespace(texture=_picture(panel)),
    )


class TestWhenTheRoomIsUp:
    def test_every_picture_present_is_a_room(self):
        assert _scene_is_up(**_room())

    @pytest.mark.parametrize("blank", ["main", "portrait", "landscape", "panel"])
    def test_one_screen_still_blank_is_not(self, blank):
        """Revealed here, that one arrives in the open a moment later — and the
        console among them, because a room with no controls in it is not up."""
        assert not _scene_is_up(**_room(**{blank: False}))

    def test_the_main_slot_counts_once_wherever_the_scene_is(self):
        """In genau mode the clip player has the scene and the video waits
        paused under it, so asking the video for a picture would hold the
        cover over a room that is finished."""
        room = _room(genau_showing=True)
        room["primary"] = SimpleNamespace(target=_picture(False))

        assert _scene_is_up(**room)


def test_the_cover_goes_up_before_the_players_are_built():
    """Built first and shown after, it was on screen for the tail of a launch
    that had already finished -- which is why none of it was ever seen."""
    import ast
    import inspect

    from fun_time_vr import player

    tree = ast.parse(inspect.getsource(player._run))
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.setdefault(ast.unparse(node.func), node.lineno)

    assert calls["_CoverUnit"] < calls["_MainUnit"]
    assert calls["_raise_the_cover"] < calls["_MainUnit"]
    assert calls["_raise_the_cover"] < calls["_SatelliteUnit"]
    assert calls["_raise_the_cover"] < calls["_PanelUnit"]


def test_the_reveal_waits_for_the_cover_to_have_been_seen():
    """The room being drawable is not the same as anyone having had the headset
    on while it was covered."""
    import ast
    import inspect

    from fun_time_vr import player

    tree = ast.parse(inspect.getsource(player._run))
    (note,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and ast.unparse(n.func) == "scene_ready.note"]

    assert "cover_seen.dwelt" in ast.unparse(note)
    assert "_scene_is_up" in ast.unparse(note)


def test_only_frames_a_worn_headset_took_count_towards_the_dwell():
    """A frame submitted while the runtime cannot locate the views, or while the
    headset is on the desk, showed nobody anything."""
    import ast
    import inspect

    from fun_time_vr import player

    tree = ast.parse(inspect.getsource(player._run))
    (note,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and ast.unparse(n.func) == "cover_seen.note"]

    assert ast.unparse(note) == "cover_seen.note(covered and session.focused)"

class TestTheMainSlotUnderThePointer:
    """The main player moves and zooms by the same handles the satellites do, so
    it is one of the screens the pointer is handed — but only while what fills
    the slot is a flat screen with edges to take hold of."""

    def _units(self, **overrides):
        settings = dict(
            picture=True, displayed=True, projection=FLAT,
            showing=False, clip=True, clip_projection=FLAT,
        ) | overrides
        primary = SimpleNamespace(
            target=SimpleNamespace(ready=settings["picture"], aspect=16 / 9),
            role=SimpleNamespace(
                displayed=settings["displayed"], projection=settings["projection"]),
            screen=SimpleNamespace(placement=DEFAULT_LAYOUT[PRIMARY]),
        )
        genau = SimpleNamespace(
            texture=SimpleNamespace(ready=settings["clip"], aspect=4 / 3),
            role=SimpleNamespace(
                showing=settings["showing"], projection=settings["clip_projection"]),
            screen=SimpleNamespace(placement=DEFAULT_LAYOUT[PRIMARY]),
        )
        return primary, genau

    def test_the_primary_offers_both_handles(self):
        screen = _main_slot_screen(*self._units())

        assert (screen.name, screen.movable, screen.resizable) == (PRIMARY, True, True)
        assert screen.placement == DEFAULT_LAYOUT[PRIMARY]
        assert screen.aspect == 16 / 9

    def test_genaus_clip_is_what_the_pointer_finds_there_while_it_has_the_scene(self):
        """It hangs in the same slot, at its own shape — so the handles stay
        under the hand through a switch into video mode and back."""
        screen = _main_slot_screen(*self._units(showing=True))

        assert screen.name == PRIMARY
        assert screen.aspect == 4 / 3

    @pytest.mark.parametrize("state", [
        {"projection": EQUIRECT_180_SBS},
        {"showing": True, "clip_projection": EQUIRECT_180_SBS},
    ])
    def test_a_wrapped_video_has_no_edges_to_take_hold_of(self, state):
        """It is round the viewer rather than hanging in the slot, so there is
        nothing to grab — but it is still what a squeeze out there lands on, so
        it stays in the scene as the screen with no rectangle."""
        screen = _main_slot_screen(*self._units(**state))

        assert (screen.movable, screen.resizable) == (False, False)
        assert (screen.pressable, screen.immersive) == (True, True)

    @pytest.mark.parametrize("state", [
        {"picture": False},
        {"displayed": False},
        {"showing": True, "clip": False},
    ])
    def test_a_slot_with_no_picture_in_it_is_not_in_the_scene_at_all(self, state):
        """Still waiting for its first frame, or standing aside for the other
        player: there is nothing there for the ray to find."""
        assert _main_slot_screen(*self._units(**state)) is None

    def test_the_main_slot_is_listed_under_the_screens_that_overlap_it(self):
        """It is drawn first and it is the biggest, so a satellite tucked over its
        edge — and the console under them all — has to win the ray.  The console
        is pressed, never dragged: it rides on the main player now."""
        satellite = SimpleNamespace(
            side=LANDSCAPE, target=SimpleNamespace(ready=True, aspect=16 / 9),
            screen=SimpleNamespace(placement=DEFAULT_LAYOUT[LANDSCAPE]), hud_ready=False,
        )
        panel = SimpleNamespace(
            texture=SimpleNamespace(ready=True, aspect=1.3),
            screen=SimpleNamespace(placement=DEFAULT_LAYOUT[PRIMARY]),
        )

        screens = _pointable_screens(*self._units(), [satellite], panel)

        assert [screen.name for screen in screens] == [PRIMARY, LANDSCAPE, PANEL]
        console = screens[-1]
        assert (console.pressable, console.movable, console.resizable) == (True, False, False)


class TestTheClipsOwnControls:
    """Genau is handed finished pictures rather than decoding its own, so its
    scrubber and volume slider are blended into the picture -- same places, same
    size, as every player that has an mpv underneath to paint them into."""

    def _unit(self, *, played=5, of=20, volume=70, muted=False):
        unit = _GenauUnit.__new__(_GenauUnit)
        unit.role = SimpleNamespace(playhead=(played, of), volume=volume, muted=muted)
        unit.screen = SimpleNamespace(placement=DEFAULT_LAYOUT[PRIMARY])
        unit._volume_painter = VolumeHudPainter()
        unit._control_size = None
        unit._scrubber_shown = unit._chip_shown = None
        unit._bar = unit._chip = None
        return unit

    def test_the_clip_comes_back_with_its_controls_on_it(self):
        unit = self._unit()
        clip = np.zeros((360, 640, 3), dtype=np.uint8)

        furnished = unit._furnished(clip)

        assert clip.max() == 0  # the engine's own picture, untouched
        assert furnished[-2].max() > 0  # a scrubber along the lower edge
        assert furnished[:200].max() == 0  # and nothing over the picture itself

    def test_the_bitmaps_are_repainted_only_when_what_they_show_moves(self):
        unit = self._unit()
        clip = np.zeros((360, 640, 3), dtype=np.uint8)

        unit._furnished(clip)
        bar, chip = unit._bar, unit._chip
        unit._furnished(clip)

        assert unit._bar is bar and unit._chip is chip

        unit.role = SimpleNamespace(playhead=(19, 20), volume=70, muted=False)
        unit._furnished(clip)

        assert unit._bar is not bar
        assert unit._chip is chip

    def test_the_press_coordinates_are_the_ones_it_drew_at(self):
        unit = self._unit()

        unit._furnished(np.zeros((360, 640, 3), dtype=np.uint8))

        assert unit._control_size == control_size(DEFAULT_LAYOUT[PRIMARY].width_deg, 640 / 360)

class _FakeRenderer:
    """Records which meshes were drawn, so a screen nobody draws is visible."""

    def __init__(self) -> None:
        self.screens: list[str] = []

    def begin_eye(self) -> None:
        pass

    def draw_screen(self, mesh, _texture, _view_proj, blend: bool = False) -> None:
        self.screens.append(mesh)

    def draw_immersive(self, *_args) -> None:
        self.screens.append("immersive")


def _hanging(mesh: str):
    return SimpleNamespace(
        texture=SimpleNamespace(ready=True, texture=object()),
        screen=SimpleNamespace(ready=True, mesh=mesh),
    )


class TestEveryHangingScreenIsDrawn:
    """The dash was pumped, pointed at and placed in the layout, and left out of
    the eye pass -- so it existed everywhere except in front of him."""

    def _views(self):
        fov = SimpleNamespace(angle_left=-0.8, angle_right=0.8,
                              angle_up=0.8, angle_down=-0.8)
        pose = SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        )
        return [SimpleNamespace(fov=fov, pose=pose)]

    def _draw(self):
        renderer = _FakeRenderer()
        session = SimpleNamespace(
            bind_eye_framebuffer=lambda _i: None, release_eye_framebuffer=lambda _i: None)
        primary = SimpleNamespace(
            target=SimpleNamespace(ready=False, texture=object()),
            screen=SimpleNamespace(ready=False, mesh="primary"),
            role=SimpleNamespace(displayed=True, projection="flat"),
        )
        genau = SimpleNamespace(
            role=SimpleNamespace(showing=False, projection="flat"),
            texture=SimpleNamespace(ready=False, texture=object()),
            screen=SimpleNamespace(ready=False, mesh="genau"),
        )
        panel, dash = _hanging("panel"), _hanging("dash")
        _draw_eyes(
            session, renderer, primary, genau, [], panel, dash,
            SimpleNamespace(draw=lambda *_a: None), self._views(), None,
            np.eye(4, dtype=np.float64), in_scene={PRIMARY, PORTRAIT, LANDSCAPE},
        )
        return renderer

    def test_the_console_reaches_the_eyes(self):
        assert "panel" in self._draw().screens

    def test_the_dashboard_reaches_them_too(self):
        assert "dash" in self._draw().screens
