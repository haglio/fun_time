from __future__ import annotations

import ast
import inspect
import json
import logging
import math
import threading
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock, call, patch

import numpy as np
import pytest
from app_support import ports
from app_support.file_channel import write_flag
from funestra.flick_overlay import FlickFrame
from funestra.host_controls import BarHud, HostControlsHud
from funestra.hud_placement import HudEdge
from funestra.main_hud import ModeHud
from funestra.main_hud_painter import MainHud
from funestra.modes import MainMode
from funestra.osr2_controls_host import Osr2ControlsHost
from funestra.outside_buttons import OutsideButtons
from funestra.play_points import play_points_filename
from funestra.playlist import PlaylistItem
from funestra.testing import FakeEngine
from shared_ui.palette import BLUE

from fun_time.config import load_config
from fun_time.dashboard_actions import (
    BROWSE_LIBRARY_CLOSE,
    HEADSET_ON,
    HELP_REFERENCE,
    LIBRARY_OPEN_FILENAME,
    QUIT_BUTTON,
    REFERENCE_OPEN_FILENAME,
)
from fun_time.event_log import MESSAGE
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    LaunchManifest,
    write_manifest_data,
)
from fun_time.osr2_section import osr2_readout_path
from fun_time.overlay_progress import (
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    SHUTDOWN_READY_FILENAME,
    PhaseProgress,
)
from fun_time.shared_state import BridgeState, shared_state_path, write_shared_state
from fun_time_vr import app, frame_loop, funestras, room
from fun_time_vr.app import build_parser
from fun_time_vr.dash_panel import (
    DASH_DEG_PER_PX,
    DASH_WIDTH_PX,
    DashState,
    dash_actions,
    dash_height,
    osr2_part_rect,
    paint_dash,
    with_the_osr2_part,
)
from fun_time_vr.funestras import (
    MAIN_TICK_HZ,
    OFF_THE_HUD,
    Arranged,
    Arrangement,
    LoadingScreenUnit,
    LoadingScreenWatch,
    VrSettings,
    _ControllerPosts,
    _DashUnit,
    _GalleryUnit,
    _hands_for_the_funestras,
    _HangingScreen,
    _LayoutKeeper,
    _LibraryUnit,
    _MainHudUnit,
    _MainUnit,
    _MessageUnit,
    _PointerDrawing,
    _ReferenceUnit,
    _SatelliteUnit,
    _still_blank,
    _users,
    _VideoUnit,
    draw_eyes,
    hang_the_room,
)
from fun_time_vr.headset import Headset
from fun_time_vr.headset_verbs import AngleRequest, HeadsetVerbs, HostRequest
from fun_time_vr.hud_buttons import HudButtons
from fun_time_vr.hud_sides import side_screen_name
from fun_time_vr.hud_toggle import HUD_GAP_PAST_THE_TOGGLE_DEG, toggle_screen_name
from fun_time_vr.layout import (
    DASH,
    GALLERY,
    LANDSCAPE,
    LIBRARY,
    MAIN,
    MAIN_HUD,
    MESSAGE_SCREEN,
    PORTRAIT,
    REFERENCE,
    Layout,
    clamp_placement,
    shown_at,
)
from fun_time_vr.library_panel import LIBRARY_SIZE_PX, scroll_from_stick, scroll_line
from fun_time_vr.loading_screen import (
    VR_SHUTDOWN_PHASES,
    VR_STARTUP_PHASES,
    WEARER_STATUS,
    LoadingScreenWatcher,
)
from fun_time_vr.main_hud import DEG_PER_PX, HUD_WIDTH_PX
from fun_time_vr.matrices import yaw_of_orientation
from fun_time_vr.messages import MessageBoard
from fun_time_vr.orchestrator import build_vr_manifest
from fun_time_vr.perf import FramePerf
from fun_time_vr.pointer import (
    DRAG,
    HANDLE_DEG,
    PRESS,
    RELEASE,
    SURFACE,
    Frame,
    HandInput,
    Hover,
    PressEvent,
    Ray,
)
from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FLAT,
    PROJECTIONS,
    RECTILINEAR_SBS,
)
from fun_time_vr.reference_panel import REFERENCE_WIDTH_DEG
from fun_time_vr.room_launch import RoomLaunch
from fun_time_vr.satellite_hud import hud_screen_name
from fun_time_vr.scene import (
    MAIN_WIDTH_DEG,
    RADIUS_M,
    Footprint,
    Placement,
    attached_below,
    attached_to,
    surface_vertices,
    widened,
)
from fun_time_vr.stacking import Stacking
from fun_time_vr.surfaces import HudBitmap, LatestFrame
from fun_time_vr.thumbs import NUDGE_FORWARD
from fun_time_vr.wrap_readout import WrapReadout, label
from fun_time_vr.wraps import immersive_wrap
from main_funestra.cli import build_parser as build_main_funestra_parser
from main_funestra.contract import MainChannels
from main_funestra.genau import ROBOT_HAND, GenauChannels
from tests.headset_layout_reports import reported_layout
from tests.made_up_genau import with_made_up_genau
from tests.made_up_osr2_parts import a_part, a_readout, band_of, center_of, handed_on, published


def test_the_funestra_is_told_its_manifest_and_nothing_else():
    args = build_parser().parse_args(["--manifest", "C:/state/windows_bridge_launch.ini"])
    assert str(args.manifest).replace("\\", "/") == "C:/state/windows_bridge_launch.ini"

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_the_vr_section_reads_back_with_its_key_case_intact(tmp_path):
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text(
        "[vr]\n"
        "library_dirs=C:/vr/one|C:/vr/two\n"
        "audio_device=Example Headset\n"
        "compositor_layers=1\n",
        encoding="utf-8")

    vr = VrSettings.read(path)

    assert [str(p).replace("\\", "/") for p in vr.library_dirs] == ["C:/vr/one", "C:/vr/two"]
    assert vr.audio_device == "Example Headset"
    assert vr.compositor_layers is True


def test_the_compositor_falls_back_to_off_when_the_section_does_not_say(tmp_path):
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text("[vr]\nlibrary_dirs=\n", encoding="utf-8")

    assert VrSettings.read(path).compositor_layers is False


def test_a_session_that_names_no_audio_device_reads_back_as_none_named(tmp_path):
    path = tmp_path / "windows_bridge_launch.ini"
    path.write_text("[vr]\nlibrary_dirs=\n", encoding="utf-8")

    assert VrSettings.read(path).audio_device == ""


_UNIT_COLLABORATORS = (
    "HeadsetEngine", "VideoThread", "RenderTarget", "FrameTexture", "Funestra",
    "HeadsetVerbs", "FrameLook", "_TheLibrary", "FunscriptTCodeDriver", "Osr2Sender",
)

_A_SESSION = VrSettings(library_dirs=(), audio_device="", compositor_layers=False)


_NO_GL_CONTEXTS = None

SPOTS = {name: spot for kind in vars(funestras).values() if isinstance(kind, type)
         for name, spot in getattr(kind, "SPOTS", {}).items()}


def _manifest_for_a_vr_session(tmp_path, *, osr2_slot_port: int = ports.OSR2_SLOT) -> LaunchManifest:
    config = load_config(Path("fun_time_config.example.json"))
    config = with_made_up_genau(
        replace(config, paths=replace(config.paths, state_dir=tmp_path, broker_state_dir=tmp_path),
                osr2_slot_port=osr2_slot_port),
        tmp_path)
    path = write_manifest_data(
        build_vr_manifest(config), tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    return LaunchManifest.read(path)


def test_a_test_sessions_channel_files_live_in_its_own_folder(tmp_path):
    manifest = _manifest_for_a_vr_session(tmp_path)

    assert Path(manifest.commands.main_funestra_cmd_file).is_relative_to(tmp_path)
    assert Path(manifest.commands.genau_cmd_file).is_relative_to(tmp_path)


@pytest.fixture
def faked_collaborators():
    with patch.multiple(
        "fun_time_vr.funestras", **dict.fromkeys(_UNIT_COLLABORATORS, DEFAULT)
    ) as fakes, \
            patch("fun_time_vr.funestras.read_playlist", return_value=[]):
        yield fakes


def _a_main_unit(tmp_path, *, remembered=None, vr=_A_SESSION, manifest=None) -> _MainUnit:
    launch = RoomLaunch(manifest or _manifest_for_a_vr_session(tmp_path), vr, _NO_GL_CONTEXTS)
    return _MainUnit(launch,
                     remembered=remembered or Layout(), stop=threading.Event())


def test_the_main_funestra_opens_where_the_last_session_left_it(tmp_path, faked_collaborators):
    moved = Placement(azimuth_deg=18.0, elevation_deg=-6.0, width_deg=95.0)

    unit = _a_main_unit(tmp_path, remembered=Layout({MAIN: moved}))

    assert unit.screen.placement == moved


def test_the_main_funestra_opens_at_the_tilt_the_last_session_left_it_at(
        tmp_path, faked_collaborators):
    _a_main_unit(tmp_path, remembered=Layout(tilt_deg=-17.5))

    assert faked_collaborators["HeadsetVerbs"].call_args.kwargs["tilt_deg"] == -17.5


def test_the_main_funestra_is_handed_every_file_it_needs_out_of_the_manifest(
        tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path)
    vr = replace(_A_SESSION, audio_device="Example Headset")

    unit = _a_main_unit(tmp_path, vr=vr, manifest=manifest)

    commands = manifest.commands
    channels = faked_collaborators["Funestra"].call_args.kwargs["channels"]
    assert channels.playlist == Path(commands.main_funestra_playlist_file)
    assert channels.command == Path(commands.main_funestra_cmd_file)
    assert channels.paused == Path(commands.main_funestra_paused_file)
    assert channels.status == Path(commands.main_funestra_status_file)
    assert channels.play_points == Path(commands.state_dir) / play_points_filename("main_player")
    assert channels.console == Path(commands.main_funestra_console_file)
    assert channels.dashboard_cmd == Path(commands.dashboard_cmd_file)
    assert channels.drive == Path(commands.genau_drive_file)
    assert channels.osr2_readout == osr2_readout_path(Path(commands.state_dir), "main")
    # The one that is not a path: without it `route_audio` never asks mpv for the
    # headset's sink, and the Main Funestra's sound stays on the room speakers.
    assert unit._audio_device == "Example Headset"


def test_the_main_funestra_is_the_desktops_with_the_headsets_own_surfaces(
        tmp_path, faked_collaborators):
    unit = _a_main_unit(tmp_path)

    handed = faked_collaborators["Funestra"].call_args.kwargs
    assert (handed["locked"], handed["sound_is_the_rooms"]) == (True, True)
    assert handed["hud_surface"] is unit.hud
    assert unit.hud.width == HUD_WIDTH_PX
    assert handed["users_frames"] is unit.users_frames
    handed["window_verbs"]("TILT_UP")
    unit.verbs.apply.assert_called_once_with("TILT_UP")


def test_its_main_hud_carries_the_projection_list_and_angle_bar_for_kinos_video(
        tmp_path, faked_collaborators):
    unit = _a_main_unit(tmp_path)
    controls = faked_collaborators["Funestra"].call_args.kwargs["host_controls"]
    unit.verbs.projection, unit.verbs.fov = EQUIRECT_180_SBS, 160.0

    unit.funestra.showing = MainMode.KINO
    with_kino = controls.hud()
    unit.funestra.showing = MainMode.GENAU
    with_genau = controls.hud()

    assert with_kino == HostControlsHud(
        "Equirectangular", tuple(label(projection) for projection in PROJECTIONS),
        bar=BarHud("Angle", 160.0, 40.0, 180.0, unit="°"))
    assert with_genau is None


def test_kino_runs_on_it_through_the_library_and_the_memory_the_desktop_reads(
        tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path)

    _a_main_unit(tmp_path, manifest=manifest)

    library = faked_collaborators["_TheLibrary"]
    assert library.call_args.args == (manifest, _A_SESSION)
    users = faked_collaborators["Funestra"].call_args.kwargs["users"]
    assert users == {MainMode.KINO: library.return_value.kino.return_value}
    assert (faked_collaborators["Funestra"].call_args.kwargs["playlist"]
            is library.return_value.playlist.return_value)


def test_the_library_names_the_sessions_message_file_to_kino(tmp_path):
    manifest = _manifest_for_a_vr_session(tmp_path)

    library = funestras._TheLibrary(manifest, _A_SESSION, while_reading=lambda: None)

    assert library.args.notice_file == Path(manifest.commands.main_funestra_notice_file)


def test_what_its_funscript_moves_say_goes_where_the_session_reads_the_main_funestras_messages(
        tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path)

    _a_main_unit(tmp_path, manifest=manifest)

    assert (faked_collaborators["Funestra"].call_args.kwargs["channels"].notice
            == Path(manifest.commands.main_funestra_notice_file))


def _every_file_a_main_funestras_command_line_can_name():
    parser = build_main_funestra_parser({})
    argv = ["--osr2-port", "51000"]
    for action in parser._actions:
        flag = next((option for option in action.option_strings if option.startswith("--")), "")
        if flag.endswith("-file") or flag in ("--playlist", "--state-dir"):
            argv += [flag, f"C:/state/{action.dest}"]
    return parser.parse_args(argv)


def _handed(channels) -> set[str]:
    return {name for name, value in vars(channels).items() if value is not None}


def test_the_main_funestra_is_handed_every_file_the_desktops_can_be_but_the_osr2s(
        tmp_path, faked_collaborators):
    _a_main_unit(tmp_path)

    headset = faked_collaborators["Funestra"].call_args.kwargs["channels"]
    desktop = MainChannels.from_args(_every_file_a_main_funestras_command_line_can_name())
    assert _handed(headset) == _handed(desktop) - {"osr2_sender", "osr2_port"}


def test_the_main_funestra_reaches_the_osr2_through_the_brokers_slot(tmp_path, faked_collaborators):
    _a_main_unit(tmp_path, manifest=_manifest_for_a_vr_session(tmp_path, osr2_slot_port=8123))

    assert faked_collaborators["Osr2Sender"].call_args == call("main", port=8123)
    assert (faked_collaborators["Funestra"].call_args.kwargs["tcode"]
            is faked_collaborators["FunscriptTCodeDriver"].return_value)


def test_the_first_move_log_passes_wanting_on_to_the_osr2(tmp_path):
    sink = MagicMock()

    funestras._SaysWhenItFirstMoves(sink, "main").want(True)

    sink.want.assert_called_once_with(True)


class TestGenauOnTheMainFunestra:
    def _users(self, tmp_path, *, flicks_folder, resumed=None):
        manifest = _manifest_for_a_vr_session(tmp_path, osr2_slot_port=8123)
        vr = replace(_A_SESSION, flicks_folder=flicks_folder)
        stop = threading.Event()
        with patch.multiple("fun_time_vr.funestras", GenauInTheHeadset=DEFAULT,
                            GenauNotifier=DEFAULT, Osr2Sender=DEFAULT, MessageWriter=DEFAULT) as fakes, \
                patch("fun_time_vr.funestras.read_genau_status",
                      return_value=SimpleNamespace(flick="")), \
                patch("fun_time_vr.funestras.read_shared_state", return_value=resumed) as read_state:
            users = _users(manifest, vr, stop, kino="kino")
            if MainMode.GENAU in users:
                users[MainMode.GENAU](None)
        return SimpleNamespace(users=users, manifest=manifest, stop=stop, fakes=fakes,
                               read_state=read_state)

    def test_a_session_naming_no_flicks_folder_runs_kino_alone(self, tmp_path):
        assert self._users(tmp_path, flicks_folder=None).users == {MainMode.KINO: "kino"}

    def test_a_session_naming_one_puts_genau_beside_kino(self, tmp_path):
        built = self._users(tmp_path, flicks_folder=tmp_path)

        assert set(built.users) == {MainMode.KINO, MainMode.GENAU}
        genau_call = built.fakes["GenauInTheHeadset"].call_args
        handed, flicks = genau_call.kwargs, genau_call.args[0]
        commands = built.manifest.commands
        assert flicks.folder == tmp_path
        assert handed["channels"] == GenauChannels(
            command=Path(commands.genau_cmd_file), paused=Path(commands.genau_paused_file),
            status=Path(commands.genau_status_file), drive=Path(commands.genau_drive_file))
        assert handed["seams"].stop_event is built.stop
        assert flicks.metadata_root == Path(built.manifest.regen.metadata_root)

    def test_it_opens_in_the_order_the_last_session_left_it_browsing(self, tmp_path):
        built = self._users(tmp_path, flicks_folder=tmp_path, resumed=BridgeState(genau_latest=True))

        assert built.read_state.call_args.args == (
            shared_state_path(Path(built.manifest.commands.state_dir)),)
        assert built.fakes["GenauInTheHeadset"].call_args.args[0].latest is True

    def test_it_opens_in_the_f_mode_it_was_left_in_over_the_rooms_favorites(self, tmp_path):
        built = self._users(tmp_path, flicks_folder=tmp_path,
                            resumed=BridgeState(genau_favorites_filter=True))

        assert built.fakes["GenauInTheHeadset"].call_args.args[0].f_mode is True

    def test_its_robot_hand_reaches_the_osr2_through_the_brokers_slot(self, tmp_path):
        built = self._users(tmp_path, flicks_folder=tmp_path)

        assert built.fakes["Osr2Sender"].call_args == call(ROBOT_HAND, port=8123)

    def test_what_it_says_goes_where_the_session_reads_the_main_funestras_messages(self, tmp_path):
        built = self._users(tmp_path, flicks_folder=tmp_path)

        assert built.fakes["MessageWriter"].call_args.args == (
            Path(built.manifest.commands.main_funestra_notice_file),)
        assert (built.fakes["GenauInTheHeadset"].call_args.kwargs["outputs"].messages
                is built.fakes["MessageWriter"].return_value)


def test_a_satellite_screen_hands_its_funestra_the_scripts_its_playlist_names(
        tmp_path, faked_collaborators):
    video, script, unscripted = tmp_path / "v0.mp4", tmp_path / "v0.funscript", tmp_path / "v1.mp4"
    items = [PlaylistItem(video, script), PlaylistItem(unscripted)]

    with patch("fun_time_vr.funestras.read_playlist", return_value=items):
        _SatelliteUnit(PORTRAIT, RoomLaunch(_manifest_for_a_vr_session(tmp_path), _A_SESSION,
                                            _NO_GL_CONTEXTS), remembered={})

    assert faked_collaborators["Funestra"].call_args.kwargs["playlist"] == items


def test_a_satellite_screen_reaches_the_osr2_through_the_brokers_slot_with_its_videos_script(
        tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path, osr2_slot_port=8123)
    _SatelliteUnit(PORTRAIT, RoomLaunch(manifest, _A_SESSION, _NO_GL_CONTEXTS), remembered={})

    assert faked_collaborators["Osr2Sender"].call_args == call(PORTRAIT, port=8123)
    assert (faked_collaborators["Funestra"].call_args.kwargs["tcode"]
            is faked_collaborators["FunscriptTCodeDriver"].return_value)


@pytest.mark.parametrize("funestra", ["portrait", "landscape"])
def test_a_satellite_unit_finds_every_file_it_needs_in_the_manifest(
        funestra, tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path)

    unit = _SatelliteUnit(
        funestra, RoomLaunch(manifest, _A_SESSION, _NO_GL_CONTEXTS), remembered={})

    commands = manifest.commands
    handed = faked_collaborators["Funestra"].call_args.kwargs
    channels = handed["channels"]
    assert channels.command == Path(commands.funestra_file(funestra, "cmd"))
    assert channels.paused == Path(commands.funestra_file(funestra, "paused"))
    assert channels.playlist == Path(commands.funestra_file(funestra, "playlist"))
    assert channels.status == Path(commands.funestra_file(funestra, "status"))
    assert channels.play_points == Path(commands.state_dir) / play_points_filename(funestra)
    assert channels.hud == Path(commands.funestra_file(funestra, "hud"))
    assert channels.dashboard_cmd == Path(commands.dashboard_cmd_file)
    assert handed["hud_surface"] is unit.hud


class _AVideoThread:
    def __init__(self, *_args, **_kwargs) -> None:
        self.engine = FakeEngine(duration_ms=600_000.0)
        self.closed = False

    @property
    def duration_ms(self) -> float:
        return self.engine.duration_ms

    def show_newest(self, _target) -> bool:
        return False

    def ask_for_a_still(self) -> None:
        pass

    def take_a_still(self):
        return None

    def close(self) -> None:
        self.closed = True


class _AKino:
    def __init__(self, _playback) -> None:
        self.closed = False

    def apply_command(self, _command: str) -> bool:
        return False

    def tick(self) -> None:
        pass

    def status_fields(self) -> dict[str, str]:
        return {}

    def top_block(self) -> ModeHud:
        return ModeHud(video="feature")

    def set_showing(self, _showing: bool) -> None:
        pass

    def own_frame(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _ALibrary:
    def __init__(self, tmp_path, *_args, **_kwargs) -> None:
        self._video = tmp_path / "feature.mp4"
        self._video.write_bytes(b"")

    def playlist(self) -> list[PlaylistItem]:
        return [PlaylistItem(self._video)]

    def kino(self):
        return _AKino


class _FakeRenderTarget:
    def __init__(self) -> None:
        self.width = self.height = 0
        self.painted = False
        self.video = None
        self.texture = 0

    ready = False
    has_frame = False
    aspect = 16 / 9

    def close(self) -> None:
        pass


@pytest.fixture
def a_fake_engine(tmp_path):
    with patch.multiple("fun_time_vr.funestras", VideoThread=_AVideoThread,
                        RenderTarget=_FakeRenderTarget, FrameTexture=_FakeTexture,
                        _TheLibrary=lambda *args, **kwargs: _ALibrary(tmp_path),
                        Osr2Sender=DEFAULT):
        yield


def _dashboard_asked(tmp_path) -> list[str]:
    path = tmp_path / "dashboard_cmd.txt"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


class TestTheMainFunestrasPass:
    def test_the_main_screen_blends_nothing_into_its_frame(self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)

        unit.pump(threading.Event(), 0.0)

        assert unit.engine.overlays == {}
        assert unit.hud.take()[0] is not None

    def test_a_squeeze_on_the_frame_asks_the_room_to_pause(self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)

        unit.point(Frame(events=(PressEvent(PRESS, MAIN, 0.5, 0.5), PressEvent(RELEASE, MAIN))))
        unit.pump(threading.Event(), 0.0)

        assert _dashboard_asked(tmp_path) == ["omnipause_toggle"]

    def test_a_squeeze_on_the_hud_does_not(self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)
        unit.pump(threading.Event(), 0.0)

        unit.point(Frame(events=(PressEvent(PRESS, MAIN_HUD, 0.999, 0.001), PressEvent(RELEASE, MAIN_HUD))))
        unit.pump(threading.Event(), 0.0)

        assert _dashboard_asked(tmp_path) == []

    def test_what_the_frame_loop_asks_is_answered_while_the_list_is_swapped_under_it(
            self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)
        unit.pump(threading.Event(), 0.0)
        answers = (unit.can_dial_the_wrap, unit.wrap_readout)

        unit.funestra.playback._playlist = []

        assert (unit.can_dial_the_wrap, unit.wrap_readout) == answers

    def test_quit_stops_the_room(self, tmp_path, a_fake_engine):
        manifest = _manifest_for_a_vr_session(tmp_path)
        unit = _a_main_unit(tmp_path, manifest=manifest)
        Path(manifest.commands.main_funestra_cmd_file).write_text("QUIT\n", encoding="utf-8")
        stop = threading.Event()

        unit.pump(stop, 0.0)

        assert stop.is_set()

    def test_closing_closes_what_runs_on_it_after_the_engine(self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)

        unit.close()

        assert unit.video.closed
        assert unit.engine.closed


class TestASqueezeOnAHangingHud:
    def _unit(self):
        presses = []
        unit = _VideoUnit.__new__(_VideoUnit)
        unit._held = False
        unit.hud = HudBitmap()
        unit.hud.overlay(10, 8, 300, np.zeros((100, 200, 4), dtype=np.uint8))
        unit.target = SimpleNamespace(width=640, height=480)
        unit.funestra = SimpleNamespace(
            press=lambda x, y, viewport: presses.append(("press", x, y, viewport)),
            motion=lambda x, y, held, viewport: presses.append(("motion", x, y, held)),
            release=lambda: presses.append(("release",)))
        return unit, presses

    def test_a_press_is_placed_at_the_huds_own_pixel_where_it_was_drawn(self):
        unit, presses = self._unit()

        unit._press_on_the_hud(PressEvent(PRESS, MAIN_HUD, 0.25, 0.5))

        assert presses == [("press", 8 + 50, 300 + 50, (640, 480))]

    def test_a_drag_keeps_the_press_held_and_a_release_lets_go(self):
        unit, presses = self._unit()

        unit._press_on_the_hud(PressEvent(PRESS, MAIN_HUD, 0.25, 0.5))
        unit._press_on_the_hud(PressEvent(DRAG, MAIN_HUD, 0.5, 0.5))
        unit._press_on_the_hud(PressEvent(RELEASE, MAIN_HUD))

        assert presses[1:] == [("motion", 8 + 100, 300 + 50, True), ("release",)]

    def test_hovering_the_hud_names_the_button_under_the_pointer(self):
        unit, presses = self._unit()

        unit._hover_over_the_hud((MAIN_HUD, (0.25, 0.5)), MAIN_HUD)

        assert presses == [("motion", 8 + 50, 300 + 50, False)]

    def test_a_pointer_off_the_hud_leaves_no_tooltip(self):
        unit, presses = self._unit()

        unit._hover_over_the_hud((MAIN, (0.25, 0.5)), MAIN_HUD)
        unit._hover_over_the_hud(None, MAIN_HUD)

        assert presses == [("motion", *OFF_THE_HUD, False)] * 2

    def test_a_held_press_is_not_moved_by_the_hover(self):
        unit, presses = self._unit()
        unit._press_on_the_hud(PressEvent(PRESS, MAIN_HUD, 0.25, 0.5))

        unit._hover_over_the_hud((MAIN_HUD, (0.5, 0.5)), MAIN_HUD)

        assert len(presses) == 1

    def test_a_press_before_the_hud_is_drawn_lands_nowhere(self):
        unit, presses = self._unit()
        unit.hud = HudBitmap()

        unit._press_on_the_hud(PressEvent(PRESS, MAIN_HUD, 0.25, 0.5))

        assert presses == []


_EVERYTHING_THE_WORKER_IS_HANDED = [
    MessageBoard, _MainUnit, _SatelliteUnit, _DashUnit, _MainHudUnit, _ReferenceUnit,
    _LibraryUnit, _MessageUnit, LoadingScreenUnit, _LayoutKeeper, _ControllerPosts,
]


class TestWhatEveryVideoUnitOwes:
    def test_the_base_refuses_to_be_pumped(self):
        unit = _VideoUnit.__new__(_VideoUnit)

        with pytest.raises(NotImplementedError):
            unit.pump(threading.Event(), 0.0)

    def test_the_base_refuses_to_be_closed(self):
        unit = _VideoUnit.__new__(_VideoUnit)

        with pytest.raises(NotImplementedError):
            unit.close()

    @pytest.mark.parametrize("unit_class", [_MainUnit, _SatelliteUnit])
    def test_both_units_answer_the_calls_the_loop_makes(self, unit_class):
        assert unit_class.pump is not _VideoUnit.pump
        assert unit_class.close is not _VideoUnit.close
        assert list(inspect.signature(unit_class.pump).parameters) == [
            "self", "stop", "now"]

    @pytest.mark.parametrize("pumped_class", _EVERYTHING_THE_WORKER_IS_HANDED)
    def test_everything_the_worker_is_handed_answers_both_calls(self, pumped_class):
        assert callable(getattr(pumped_class, "pump", None))
        assert callable(getattr(pumped_class, "close", None))


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
    screen = _HangingScreen(SPOTS[LANDSCAPE])
    screen.mesh = _FakeMesh()

    screen.rehang(4 / 3)
    screen.rehang(4 / 3)
    assert len(screen.mesh.uploads) == 1

    screen.placement = Placement(azimuth_deg=50.0, elevation_deg=0.0, width_deg=40.0)
    screen.rehang(4 / 3)
    screen.rehang(4 / 3)

    assert len(screen.mesh.uploads) == 2
    assert not np.array_equal(screen.mesh.uploads[0], screen.mesh.uploads[1])


def _a_satellite_unit(tmp_path, funestra, remembered):
    vr = VrSettings(library_dirs=(), audio_device="", compositor_layers=False)
    launch = RoomLaunch(_manifest_for_a_vr_session(tmp_path), vr, _NO_GL_CONTEXTS)
    return _SatelliteUnit(funestra, launch, remembered=remembered)


def test_a_satellite_hangs_where_the_layout_says(tmp_path, faked_collaborators):
    moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

    unit = _a_satellite_unit(tmp_path, PORTRAIT, {PORTRAIT: moved})

    assert unit.screen.placement == moved


def test_a_satellite_the_layout_says_nothing_about_hangs_in_its_own_spot(
        tmp_path, faked_collaborators):
    landscape_only = {LANDSCAPE: Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)}

    unit = _a_satellite_unit(tmp_path, PORTRAIT, landscape_only)

    assert unit.screen.placement == _SatelliteUnit.SPOTS[PORTRAIT]


class TestTheLayoutKeeper:
    def test_what_the_controllers_settled_is_written_once_on_the_worker(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())
        moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

        keeper.pump(threading.Event(), 0.0)
        assert not path.exists()

        keeper.place(PORTRAIT, moved)
        assert not path.exists()

        keeper.settle()
        keeper.pump(threading.Event(), 0.0)
        assert reported_layout(path).placements[PORTRAIT] == moved

        written = path.stat().st_mtime_ns
        keeper.pump(threading.Event(), 0.0)
        assert path.stat().st_mtime_ns == written

    def test_the_tilt_the_room_was_left_at_is_written_beside_the_screens(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())
        moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

        keeper.place(PORTRAIT, moved)
        keeper.tilt(12.5, carried=False)
        keeper.settle()
        keeper.pump(threading.Event(), 0.0)

        assert reported_layout(path) == Layout({PORTRAIT: moved}, 12.5)

    def test_being_told_the_tilt_it_already_holds_is_not_worth_a_file(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout(tilt_deg=12.5))

        keeper.tilt(12.5, carried=False)
        keeper.close()

        assert not path.exists()

    def test_a_tilt_off_a_keypress_is_finished_the_moment_it_lands(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())

        keeper.tilt(5.0, carried=False)
        keeper.pump(threading.Event(), 0.0)

        assert reported_layout(path).tilt_deg == 5.0

    def test_a_tilt_a_hand_is_still_lifting_waits_for_the_release(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())

        keeper.tilt(5.0, carried=True)
        keeper.pump(threading.Event(), 0.0)
        assert not path.exists()

        keeper.settle()
        keeper.pump(threading.Event(), 0.0)

        assert reported_layout(path).tilt_deg == 5.0

    def test_a_session_ending_mid_drag_still_keeps_the_screen_where_it_was_left(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())
        moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

        keeper.place(PORTRAIT, moved)
        keeper.close()

        assert reported_layout(path).placements[PORTRAIT] == moved

    def test_putting_the_room_back_leaves_the_session_remembering_nothing(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())
        keeper.place(PORTRAIT, Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0))
        keeper.tilt(-20.0, carried=False)
        keeper.settle()
        keeper.pump(threading.Event(), 0.0)
        assert reported_layout(path) != Layout()

        keeper.forget()
        keeper.settle()
        keeper.pump(threading.Event(), 0.0)

        assert reported_layout(path) == Layout()


class TestWhatTheControllersPost:
    def test_a_press_the_render_thread_hands_over_is_posted_on_the_worker(self, tmp_path):
        command_file = tmp_path / "dashboard_cmd.txt"
        posts = _ControllerPosts(command_file)

        posts.post(("main_nudge_next", "main_scene_prev"))
        assert not command_file.exists()

        posts.pump(threading.Event(), 0.0)

        assert command_file.read_text(encoding="utf-8").split() == [
            "main_nudge_next", "main_scene_prev"]

    def test_a_press_made_as_the_session_ends_is_still_posted(self, tmp_path):
        command_file = tmp_path / "dashboard_cmd.txt"
        posts = _ControllerPosts(command_file)

        posts.post(("main_scene_next",))
        posts.close()

        assert command_file.read_text(encoding="utf-8").split() == ["main_scene_next"]


_THE_DASHS_SHAPE = DASH_WIDTH_PX / dash_height()


class _FakeMainHudTexture:
    ready = True
    aspect = 280 / 120
    width = HUD_WIDTH_PX

    def upload(self, pixels):
        self.uploaded = pixels

    def close(self):
        pass


class TestTheConsoleUnderTheSlot:
    def _unit(self, *, wrapped=False, showing=False, edge=HudEdge.LOWER, minimized=False):
        projection = EQUIRECT_180_SBS if wrapped else FLAT
        posted: list[str] = []
        main_unit = _like(_MainUnit, _AMainStandIn(
            owns_the_slot=not showing,
            funestra=SimpleNamespace(hud_edge=edge, hud_minimized=minimized,
                                     outside_buttons=OutsideButtons(MAIN, minimized=minimized)),
            _post=posted.append,
            hud=HudBitmap(width=HUD_WIDTH_PX),
            verbs=_VerbsStandIn(projection_of=lambda _video: projection, **_NEVER_DIALED),
            target=SimpleNamespace(ready=True, aspect=16 / 9, video=None),
            users_frame_texture=SimpleNamespace(ready=True, aspect=4 / 3),
            _flick_projection=(Path("flick.mp4"), projection),
            screen=SimpleNamespace(placement=SPOTS[MAIN]),
        ))
        dash = SimpleNamespace(texture=SimpleNamespace(ready=True, aspect=_THE_DASHS_SHAPE),
                               screen=SimpleNamespace(placement=SPOTS[MAIN_HUD]))
        with patch("fun_time_vr.funestras.FrameTexture", _FakeMainHudTexture):
            unit = _MainHudUnit(main_unit, dash)
        if not minimized:
            main_unit.hud.overlay(10, 8, 8, np.zeros((120, HUD_WIDTH_PX, 4), dtype=np.uint8))
        return SimpleNamespace(unit=unit, main_unit=main_unit, dash=dash, posted=posted)

    def test_a_minimized_main_hud_is_not_one_of_the_rooms_screens(self):
        assert not self._unit(minimized=True).unit.in_the_room
        assert self._unit().unit.in_the_room

    @staticmethod
    def _hanging_names(unit) -> list[str]:
        return [hanging.screen.name for hanging in unit.hangings()]

    @staticmethod
    def _placed(unit) -> Placement:
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        return unit.screen.placement

    def test_the_bitmap_the_main_funestra_drew_is_what_it_hangs(self):
        p = self._unit()

        self._placed(p.unit)

        assert p.unit.texture.uploaded.shape == (120, HUD_WIDTH_PX, 4)

    def test_the_main_hud_rides_under_the_main_funestra_wherever_it_goes(self):
        p = self._unit()

        docked = self._placed(p.unit)
        p.main_unit.screen.placement = Placement(
            azimuth_deg=-40.0, elevation_deg=12.0, width_deg=110.0)
        followed = self._placed(p.unit)

        assert docked.azimuth_deg == SPOTS[MAIN].azimuth_deg
        assert docked.elevation_deg < 0.0
        assert followed.azimuth_deg == -40.0
        assert followed.elevation_deg < docked.elevation_deg
        assert followed.width_deg == docked.width_deg == HUD_WIDTH_PX * DEG_PER_PX

    def test_it_rides_under_genaus_frame_at_the_flicks_own_shape(self):
        p = self._unit(showing=True)

        placed = self._placed(p.unit)

        flick = p.main_unit.users_frame_texture.aspect
        assert placed == attached_below(
            shown_at(MAIN, p.main_unit.screen.placement, flick), aspect=flick,
            shape=Footprint(HUD_WIDTH_PX * DEG_PER_PX, _FakeMainHudTexture.aspect),
            gap_deg=HUD_GAP_PAST_THE_TOGGLE_DEG)

    def test_it_hangs_along_the_edge_the_room_moved_it_to(self):
        p = self._unit(edge=HudEdge.UPPER)

        placed = self._placed(p.unit)

        assert placed.elevation_deg > 0.0

    def test_a_wrapped_main_hud_hangs_from_the_dashboard_instead(self):
        p = self._unit(wrapped=True, edge=HudEdge.UPPER)

        placed = self._placed(p.unit)

        assert placed == attached_below(
            p.dash.screen.placement, aspect=p.dash.texture.aspect,
            shape=Footprint(HUD_WIDTH_PX * DEG_PER_PX, _FakeMainHudTexture.aspect),
            gap_deg=HUD_GAP_PAST_THE_TOGGLE_DEG)

    @pytest.mark.parametrize("wrapped", [False, True])
    def test_its_minus_hangs_outside_it(self, wrapped):
        p = self._unit(wrapped=wrapped)

        self._placed(p.unit)

        assert toggle_screen_name(MAIN) in self._hanging_names(p.unit)

    def test_under_the_dashboard_its_minus_hangs_between_the_two(self):
        p = self._unit(wrapped=True)

        placed = self._placed(p.unit)

        minus, = (hanging for hanging in p.unit.hangings()
                  if hanging.screen.name == toggle_screen_name(MAIN))
        over = surface_vertices(p.dash.screen.placement, aspect=p.dash.texture.aspect)
        button = surface_vertices(minus.screen.placement, aspect=1.0)
        main_hud_unit = surface_vertices(placed, aspect=_FakeMainHudTexture.aspect)
        assert over[:, 1].min() > button[:, 1].max() > button[:, 1].min() > main_hud_unit[:, 1].max()

    def test_beside_a_wrapped_video_there_is_no_satellite_to_move_it_to(self):
        p = self._unit(wrapped=True)

        self._placed(p.unit)

        assert side_screen_name(MAIN, HudEdge.UPPER) not in self._hanging_names(p.unit)

    def test_minimized_only_its_plus_hangs_with_the_satellites_it_could_open_on(self):
        p = self._unit(minimized=True)

        self._placed(p.unit)

        names = self._hanging_names(p.unit)
        assert MAIN_HUD not in names
        assert toggle_screen_name(MAIN) in names
        assert side_screen_name(MAIN, HudEdge.UPPER) in names

    def test_a_squeeze_on_its_minus_asks_the_room_to_minimize_it(self):
        p = self._unit()
        self._placed(p.unit)

        p.unit.point(Frame(events=(PressEvent(PRESS, toggle_screen_name(MAIN)),)))
        p.unit.pump(threading.Event(), 0.0)

        assert p.posted == ["main_hud_minimize"]

    def test_it_is_pressed_and_never_dragged(self):
        p = self._unit()
        self._placed(p.unit)

        hanging, = (one for one in p.unit.hangings() if one.screen.name == MAIN_HUD)

        assert hanging.screen.name == MAIN_HUD
        assert hanging.screen.pressable
        assert not hanging.screen.movable
        assert hanging.docked_to == MAIN

    def test_before_the_main_funestra_has_drawn_it_there_is_nothing_to_hang(self):
        p = self._unit()
        p.main_unit.hud = HudBitmap()
        p.unit.texture = _FakeTexture()

        self._placed(p.unit)

        assert p.unit.hangings() == ()


class TestASatellitesButtonsInTheHeadset:
    @staticmethod
    def _unit(tmp_path, *, minimized=False):
        unit = _a_satellite_unit(tmp_path, PORTRAIT, {})
        unit.funestra = SimpleNamespace(hud_edge=HudEdge.LEFT, hud_minimized=minimized,
                                        outside_buttons=OutsideButtons(PORTRAIT, minimized=minimized),
                                        tick=lambda viewport: None,
                                        motion=lambda x, y, held, viewport: None)
        unit.video = SimpleNamespace(show_newest=lambda _target: False)
        unit.target = SimpleNamespace(ready=True, aspect=16 / 9, width=640, height=480)
        unit.hud_texture = _FakeMainHudTexture()
        if not minimized:
            unit.hud.overlay(10, 0, 0, np.zeros((120, 280, 4), dtype=np.uint8))
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        return unit

    @staticmethod
    def _hanging_names(unit) -> list[str]:
        return [hanging.screen.name for hanging in unit.hangings()]

    def test_its_hud_hangs_past_the_minus_beside_the_frame(self, tmp_path, faked_collaborators):
        unit = self._unit(tmp_path)

        assert unit.hud_screen.placement == attached_to(
            HudEdge.LEFT, unit.shown, aspect=16 / 9,
            shape=Footprint(_FakeMainHudTexture.width * DEG_PER_PX, _FakeMainHudTexture.aspect),
            gap_deg=HUD_GAP_PAST_THE_TOGGLE_DEG)
        assert {toggle_screen_name(PORTRAIT), hud_screen_name(PORTRAIT)} <= set(self._hanging_names(unit))

    def test_minimized_its_plus_hangs_where_the_minus_was_and_the_hud_does_not(
            self, tmp_path, faked_collaborators):
        unit = self._unit(tmp_path, minimized=True)

        names = self._hanging_names(unit)
        assert hud_screen_name(PORTRAIT) not in names
        assert toggle_screen_name(PORTRAIT) in names

    def test_a_squeeze_on_a_satellite_asks_the_room_for_the_hud_there(
            self, tmp_path, faked_collaborators):
        unit = self._unit(tmp_path)

        unit.point(Frame(events=(PressEvent(PRESS, side_screen_name(PORTRAIT, HudEdge.RIGHT)),)))
        unit.pump(threading.Event(), 0.0)

        assert _dashboard_asked(tmp_path) == ["portrait_hud_restore_at|right"]


class _FakeTexture:
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
def loading_screen_graphics():
    with patch("fun_time_vr.funestras.FrameTexture", _FakeTexture),             patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
        yield


class TestTheLoadingScreenUnit:
    def test_an_idle_state_dir_shows_the_scene(self, tmp_path, loading_screen_graphics):
        unit = LoadingScreenUnit(tmp_path)

        unit.render_latest_frame()

        assert not unit.showing
        assert not unit.closing

    def test_a_launch_in_progress_is_covered_from_the_first_frame(
            self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("funestras")

        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()

        assert unit.showing
        assert len(unit.texture.uploads) == 1

    def test_the_bitmap_is_uploaded_once_per_change_not_per_frame(
            self, tmp_path, loading_screen_graphics):
        progress = PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=VR_STARTUP_PHASES)
        progress.advance("funestras")
        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()
        unit.render_latest_frame()
        assert len(unit.texture.uploads) == 1

        progress.advance("finalizing")
        unit.pump(threading.Event(), 0.0)
        unit.render_latest_frame()

        assert len(unit.texture.uploads) == 2

    def test_it_says_it_is_waiting_on_him_once_the_room_is_up(
            self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("funestras")
        unit = LoadingScreenUnit(tmp_path)
        assert unit._loading_screen.status == "Waiting for Funestras..."

        unit.awaiting_wearer = True
        unit.pump(threading.Event(), 0.0)

        assert unit._loading_screen.status == WEARER_STATUS
        assert unit._loading_screen.fraction == pytest.approx(
            LoadingScreenWatcher(tmp_path).read().fraction
        ), "only the words change; the bar still says how far the launch got"

    def test_a_teardown_never_asks_him_to_put_it_on(self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("funestras")
        unit = LoadingScreenUnit(tmp_path)

        unit.awaiting_wearer = True
        unit.pump(threading.Event(), 0.0)

        assert unit._loading_screen.status != WEARER_STATUS

    def test_done_hands_the_headset_back(self, tmp_path, loading_screen_graphics):
        progress = PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=VR_STARTUP_PHASES)
        progress.advance("finalizing")
        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()
        assert unit.showing

        progress.finish()
        unit.pump(threading.Event(), 0.0)
        unit.render_latest_frame()

        assert not unit.showing

    def test_the_ready_flag_waits_for_a_closing_screen(self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("funestras")
        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()

        unit.settled()

        assert not (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_a_painted_closing_screen_reports_itself(self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("controls")
        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()
        assert unit.closing

        unit.settled()

        assert (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_a_closing_screen_answers_teardown_before_a_frame_is_drawn(
            self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("controls")

        unit = LoadingScreenUnit(tmp_path)

        assert unit.closing
        assert not unit.showing
        unit.settled()
        assert (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_an_idle_state_dir_wants_no_loading_screen_at_all(self, tmp_path, loading_screen_graphics):
        assert not LoadingScreenUnit(tmp_path).wanted

    def test_a_launch_in_progress_wants_one(self, tmp_path, loading_screen_graphics):
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("funestras")

        assert LoadingScreenUnit(tmp_path).wanted

    def test_the_anchor_is_let_go_when_the_loading_screen_does(self, tmp_path, loading_screen_graphics):
        progress = PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=VR_STARTUP_PHASES)
        progress.advance("funestras")
        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()
        assert unit.anchor.heading(1.2) == pytest.approx(1.2)

        progress.finish()
        unit.pump(threading.Event(), 0.0)
        unit.render_latest_frame()

        assert unit.anchor.heading(2.9) == pytest.approx(2.9)

    def test_the_funestra_ending_on_its_own_raises_its_own_closing_screen(
            self, tmp_path, loading_screen_graphics):
        unit = LoadingScreenUnit(tmp_path)
        unit.render_latest_frame()
        assert not unit.showing

        unit.closing_now()
        unit.refresh()
        unit.render_latest_frame()

        assert unit.showing
        assert unit.closing


def _frame(painted):
    return SimpleNamespace(ready=True, has_frame=painted)


def _room(*, main=True, portrait=True, landscape=True, main_hud_unit=True, main_hud_in_the_room=True):
    return {
        "main_unit": SimpleNamespace(screen_name="main", texture_on_the_screen=_frame(main)),
        "satellites": [SimpleNamespace(screen_name="portrait", target=_frame(portrait)),
                    SimpleNamespace(screen_name="landscape", target=_frame(landscape))],
        "main_hud_unit": SimpleNamespace(texture=_frame(main_hud_unit), in_the_room=main_hud_in_the_room),
    }


class TestWhenTheRoomIsUp:
    def test_every_frame_present_leaves_nothing_blank(self):
        assert _still_blank(**_room()) == ()

    @pytest.mark.parametrize(("blank", "screen"), [
        ("main", MAIN), ("portrait", PORTRAIT), ("landscape", LANDSCAPE), ("main_hud_unit", MAIN_HUD)])
    def test_a_screen_with_no_frame_is_named(self, blank, screen):
        assert _still_blank(**_room(**{blank: False})) == (screen,)

    def test_a_main_hud_the_room_does_not_show_is_not_waited_for(self):
        assert _still_blank(**_room(main_hud_unit=False, main_hud_in_the_room=False)) == ()

    def test_a_sized_texture_is_not_a_frame(self):
        scene = _room()
        scene["main_unit"] = SimpleNamespace(screen_name="main", texture_on_the_screen=(
            SimpleNamespace(ready=True, has_frame=False)))

        assert _still_blank(**scene) == ("main",)

    def test_the_main_funestra_counts_once_wherever_the_scene_is(self):
        unit = _like(_MainUnit, SimpleNamespace(
            owns_the_slot=False, target=_frame(False),
            users_frame_texture=_frame(True)))

        assert _MainUnit.texture_on_the_screen.fget(unit) is unit.users_frame_texture


def test_the_loading_screen_goes_up_before_the_funestras_are_built():
    tree = ast.parse(inspect.getsource(app._run))
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.setdefault(ast.unparse(node.func), node.lineno)

    assert calls["LoadingScreenUnit"] < calls["raise_the_loading_screen"] < calls["hang_the_room"]


def test_the_eyes_are_handed_the_arrangement_the_pointer_was():
    tree = ast.parse(inspect.getsource(frame_loop.FrameLoop.show_a_frame).lstrip())
    (drawn,) = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == "draw_eyes"]

    assert [ast.unparse(arg) for arg in drawn.args[:2]] == ["headset", "arranged"]
    assert {k.arg: ast.unparse(k.value) for k in drawn.keywords} == {"as_quads": "as_quads"}


def test_the_headset_builds_kino_with_the_desktops_own_builder():
    tree = ast.parse(inspect.getsource(funestras._TheLibrary.kino).lstrip())
    calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}

    assert "kino_on" in calls


def _keyword_given(source_of, call: str, keyword: str) -> str:
    tree = ast.parse(inspect.getsource(source_of).lstrip())
    (built,) = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call) and ast.unparse(node.func) == call]
    return next(ast.unparse(given.value) for given in built.keywords if given.arg == keyword)


def test_the_headsets_genau_keeps_its_flips_where_the_library_keeps_its_records():
    assert _keyword_given(funestras._users, "GenauFlicks", "metadata_root") == (
        "_metadata_root(manifest)")


def test_the_headset_session_runs_ahead_of_background_work():
    tree = ast.parse(inspect.getsource(app.main))
    (scheduled,) = [n for n in ast.walk(tree)
                    if isinstance(n, ast.With)
                    and [ast.unparse(item.context_expr) for item in n.items]
                    == ["ahead_of_background_work()"]]

    assert ast.unparse(scheduled.body) == "return _run(manifest, vr, args.manifest)"


def _hang_a_room(tmp_path, *, present_the_loading_screen=lambda: None, loading_screen=None):
    manifest = _manifest_for_a_vr_session(tmp_path)
    with patch("fun_time_vr.funestras.LibraryHost"), \
            patch("fun_time_vr.funestras.UnloggedMessages"), \
            patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
        return hang_the_room(
            RoomLaunch(manifest, _A_SESSION, _NO_GL_CONTEXTS), manifest_path=tmp_path / "launch.ini",
            loading_screen=loading_screen or LoadingScreenUnit(tmp_path), present_the_loading_screen=present_the_loading_screen)


def _kinds_of_screen() -> dict[str, type]:
    return {name: thing for name, thing in vars(funestras).items()
            if isinstance(thing, type) and name.endswith("Unit")}


class TestHangingTheRoom:
    def test_it_holds_one_of_every_kind_of_screen_but_the_shared_base(
            self, tmp_path, faked_collaborators):
        kinds = _kinds_of_screen()
        held = {name for name, kind in kinds.items()
                if not any(other is not kind and issubclass(other, kind)
                           for other in kinds.values())}
        assert len(held) >= 8, "the gate below passes trivially if this ever comes back empty"

        the_room = _hang_a_room(tmp_path)

        assert {type(unit).__name__ for unit in the_room.units} == held

    def test_everything_it_holds_answers_every_question_it_is_asked(
            self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)

        for unit in the_room.units:
            for asked in ("hangings", "hangs_by", "put_back", "point", "pump",
                          "render_latest_frame", "close"):
                assert callable(getattr(unit, asked, None)), (type(unit).__name__, asked)

    def test_the_loading_screen_it_is_handed_hangs_in_it(self, tmp_path, faked_collaborators):
        loading_screen = LoadingScreenUnit(tmp_path)

        the_room = _hang_a_room(tmp_path, loading_screen=loading_screen)

        assert the_room.loading_screen is loading_screen
        assert loading_screen in the_room.units

    def test_every_unit_is_asked_for_its_latest_frame_each_frame(
            self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)
        asked: list = []
        for unit in the_room.units:
            unit.render_latest_frame = partial(asked.append, unit)

        the_room.render_latest_frames()

        assert asked == the_room.units

    def test_the_sound_goes_live_on_every_video_at_once(self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)
        routed: list = []
        for unit in the_room.videos:
            unit.route_audio = partial(routed.append, unit)

        the_room.route_audio()

        assert routed == [the_room.main_unit, *the_room.satellites]

    def test_it_is_the_one_list_everything_is_read_off(self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)

        assert all(unit in the_room.closing for unit in the_room.units)
        assert the_room.pumped == [one for one in the_room.closing if one is not the_room.main_unit]
        assert set(the_room.where) == set(room.where_they_hang(the_room.units))
        assert the_room.hangings() == room.what_hangs(the_room.units)

    def test_everything_it_closes_when_it_ends_has_a_close(self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)

        assert any(isinstance(one, MessageBoard) for one in the_room.closing)
        assert the_room.posts in the_room.closing
        assert [type(one).__name__ for one in the_room.closing
                if not callable(getattr(one, "close", None))] == []

    def _closed_by(self, the_room, *, keep_the_loading_screen: bool) -> list:
        closed: list = []
        for one in the_room.closing:
            one.close = partial(closed.append, one)
        the_room.close(keep_the_loading_screen=keep_the_loading_screen)
        return closed

    def test_closing_it_closes_everything_it_holds(self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)

        assert self._closed_by(the_room, keep_the_loading_screen=False) == the_room.closing

    def test_a_loading_screen_kept_up_for_the_next_session_is_the_one_thing_left_open(
            self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)

        closed = self._closed_by(the_room, keep_the_loading_screen=True)

        assert the_room.loading_screen not in closed
        assert closed == [one for one in the_room.closing if one is not the_room.loading_screen]

    def test_the_main_funestra_ticks_on_a_worker_of_its_own_at_genaus_rate(
            self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)
        perf = FramePerf(logger=funestras.logger)
        started: list[dict] = []

        with patch("fun_time_vr.funestras.start_daemon_thread",
                   side_effect=lambda **how: started.append(how) or SimpleNamespace(**how)):
            the_room.start_pumping(perf)

        by_name = {one["name"]: one for one in started}
        assert by_name["main-funestra"]["args"] == ([the_room.main_unit], the_room.stop, perf)
        assert by_name["main-funestra"]["kwargs"] == {"hz": MAIN_TICK_HZ}
        assert by_name["file-channels"]["args"] == (the_room.pumped, the_room.stop, perf)
        assert all(one["target"] is funestras._pump_channels for one in started)
        assert len(the_room.workers) == len(started) == 2

    def test_the_browse_asks_whether_genau_has_the_main_funestra(self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)

        faked_collaborators["Funestra"].return_value.showing = MainMode.KINO
        assert the_room.library._genau_has_the_slot() is False
        faked_collaborators["Funestra"].return_value.showing = MainMode.GENAU
        assert the_room.library._genau_has_the_slot() is True

    def test_the_main_funestra_keeps_the_loading_screen_moving_while_it_reads_the_library(
            self, tmp_path, faked_collaborators):
        present = MagicMock()

        _hang_a_room(tmp_path, present_the_loading_screen=present)

        assert faked_collaborators["_TheLibrary"].call_args.kwargs["while_reading"] is present

    def test_the_loading_screen_is_presented_between_the_funestras_being_built(
            self, tmp_path, faked_collaborators):
        order: list[str] = []
        faked_collaborators["Funestra"].side_effect = (
            lambda *_args, **_kwargs: order.append("funestra") or MagicMock())

        _hang_a_room(tmp_path, present_the_loading_screen=lambda: order.append("loading_screen"))

        assert order == ["funestra", "loading_screen", "funestra", "funestra", "loading_screen"]

    def test_it_opens_its_screens_where_the_last_session_left_them(
            self, tmp_path, faked_collaborators):
        moved = Placement(azimuth_deg=18.0, elevation_deg=-6.0, width_deg=95.0)
        write_shared_state(shared_state_path(tmp_path), BridgeState(headset_layout=json.dumps({
            MAIN: {"azimuth_deg": 18.0, "elevation_deg": -6.0, "width_deg": 95.0}})))

        the_room = _hang_a_room(tmp_path)

        assert the_room.main_unit.screen.placement == moved

    def test_what_the_headset_says_about_its_wearer_goes_to_the_dashboard(
            self, tmp_path, faked_collaborators):
        the_room = _hang_a_room(tmp_path)
        posted: list[str] = []
        the_room.posts.post = posted.extend

        the_room.note_the_wearer(True, now=10.0)

        assert posted == [HEADSET_ON]


class TestTheShellsWordsForTheRoom:
    def _answers(self, tmp_path):
        the_room = _hang_a_room(tmp_path)
        return {
            "the_room": the_room,
            "arranging": Arrangement(the_room),
            "arranged": Arranged([], [], np.eye(4, dtype=np.float32), 0.0, 0.0),
            "loading_screen": the_room.loading_screen,
        }

    @staticmethod
    def _reads(tree) -> list[tuple[str, ...]]:
        reads = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            chain = []
            reached = node
            while isinstance(reached, ast.Attribute):
                chain.append(reached.attr)
                reached = reached.value
            if isinstance(reached, ast.Name):
                reads.append((reached.id, *reversed(chain)))
        return reads

    def test_every_name_the_shell_reads_off_the_room_is_one_it_answers(
            self, tmp_path, faked_collaborators):
        answers = self._answers(tmp_path)
        unanswered = set()
        for root, *attrs in self._reads(ast.parse(inspect.getsource(app))):
            if root not in answers:
                continue
            found = answers[root]
            for attr in attrs:
                if not hasattr(found, attr):
                    unanswered.add(".".join([root, *attrs]))
                    break
                found = getattr(found, attr)

        assert unanswered == set(), (
            "the shell reads these off the room and nothing there answers them: "
            f"{sorted(unanswered)}")


class TestWatchingTheLoadingScreen:
    def _watch(self, *, dwell_s: float = 0.0):
        loading_screen = SimpleNamespace(anchor_settled=None, awaiting_wearer=None, settled=MagicMock())
        scene_ready = MagicMock()
        watch = LoadingScreenWatch(loading_screen, scene_ready, dwell_s=dwell_s)
        return loading_screen, scene_ready, watch

    def test_the_reveal_waits_for_the_loading_screen_to_have_been_on_screen_not_for_a_wearer(self):
        _loading_screen, scene_ready, watch = self._watch()

        watch.frame_ended(covered=True, focused=False, should_render=True, now=1.0)
        watch.frame_began(blank=(), focused=False, views_tracked=True)

        scene_ready.note.assert_called_with(blank=(), read=True)

    def test_a_blank_screen_is_named_to_the_reveal(self):
        _loading_screen, scene_ready, watch = self._watch()

        watch.frame_began(blank=("portrait",), focused=False, views_tracked=True)

        scene_ready.note.assert_called_with(blank=("portrait",), read=False)

    def test_the_loading_screen_holds_its_heading_once_a_wearer_has_had_time_at_it(self):
        loading_screen, _scene_ready, watch = self._watch()

        watch.frame_ended(covered=True, focused=False, should_render=True, now=1.0)
        watch.frame_began(blank=(), focused=False, views_tracked=True)
        unworn = loading_screen.anchor_settled
        watch.frame_ended(covered=True, focused=True, should_render=True, now=2.0)
        watch.frame_began(blank=(), focused=True, views_tracked=True)
        worn = loading_screen.anchor_settled
        watch.frame_began(blank=(), focused=True, views_tracked=False)

        assert (unworn, worn, loading_screen.anchor_settled) == (False, True, False)

    def test_the_loading_screen_is_told_it_is_waiting_on_him_only_once_the_room_is_up(self):
        loading_screen, _scene_ready, watch = self._watch()

        watch.frame_began(blank=("main",), focused=False, views_tracked=True)
        building = loading_screen.awaiting_wearer
        watch.frame_began(blank=(), focused=False, views_tracked=True)
        built = loading_screen.awaiting_wearer
        watch.frame_began(blank=(), focused=True, views_tracked=True)

        assert (building, built, loading_screen.awaiting_wearer) == (False, True, False)

    def test_the_loading_screen_settles_on_a_frame_it_reached_or_that_nothing_reached(self):
        loading_screen, _scene_ready, watch = self._watch()

        watch.frame_ended(covered=False, focused=True, should_render=True, now=1.0)
        assert not loading_screen.settled.called
        watch.frame_ended(covered=True, focused=True, should_render=True, now=2.0)
        watch.frame_ended(covered=False, focused=True, should_render=False, now=3.0)

        assert loading_screen.settled.call_count == 2

    def test_it_says_once_when_the_loading_screen_went_up_and_once_when_it_came_down(self, caplog):
        _loading_screen, _scene_ready, watch = self._watch()

        with caplog.at_level(logging.INFO, logger="fun_time_vr.funestras"):
            watch.drawing(now=10.0)
            watch.drawing(now=11.0)
            watch.frame_ended(covered=False, focused=True, should_render=True, now=12.5)
            watch.frame_ended(covered=False, focused=True, should_render=True, now=13.5)

        assert caplog.messages == ["Loading screen drawing to the headset", "Loading screen down after 2.5s on screen"]


def _a_view(*, yaw_deg: float = 0.0):
    half = math.radians(yaw_deg) / 2
    return SimpleNamespace(pose=SimpleNamespace(
        position=SimpleNamespace(x=0.0, y=1.6, z=0.0),
        orientation=SimpleNamespace(x=0.0, y=math.sin(half), z=0.0, w=math.cos(half))))


class _APointer:
    squeezing = False

    def __init__(self, *frames: Frame) -> None:
        self._frames = list(frames)
        self.let_go_asked = 0
        self.handed: list = []

    def let_go(self) -> None:
        self.let_go_asked += 1

    def spend_the_squeeze(self) -> None:
        pass

    def frame(self, hands, *, head, scene_rotation, screens) -> Frame:
        self.handed.append(list(screens))
        return self._frames.pop(0) if self._frames else Frame()


def _a_room_to_arrange(*, main=None, satellites=(), others=()):
    main = main or _a_main_funestra()
    main.verbs.recenter = HostRequest()
    main.verbs.layout_reset = HostRequest()
    main.verbs.angle_asked = AngleRequest()
    main.verbs.tilt_deg = 0.0
    main.verbs.nudge_tilt = MagicMock()
    main.can_dial_the_wrap = False
    main.dial_the_wrap = MagicMock()
    main.flash_the_readout = MagicMock()
    satellites = list(satellites) or [_a_satellite(PORTRAIT), _a_satellite(LANDSCAPE)]
    units = [main, *satellites, *others]
    for unit in units:
        unit.point = MagicMock()
        unit.put_back = MagicMock()
    return SimpleNamespace(
        main_unit=main, satellites=satellites, units=units,
        where=room.where_they_hang(units), hangings=lambda: room.what_hangs(units),
        library=SimpleNamespace(takes_the_stick=False),
        gallery=SimpleNamespace(takes_the_stick=False),
        posts=MagicMock(), keeper=MagicMock(), pointing=MagicMock(),
    )


class TestArrangingTheRoom:
    def _arrange(self, the_room, pointer=None, *, hands=None, views=None, now=5.0):
        arranging = Arrangement(the_room, pointer=pointer or _APointer())
        return arranging, arranging.arrange(
            hands or {}, views or [_a_view()], elapsed_s=1 / 90, now=now)

    def test_a_carry_turns_the_room_whatever_the_main_funestra_is_showing(self):
        turned = []
        for projection in (FLAT, EQUIRECT_180_SBS):
            the_room = _a_room_to_arrange(main=_a_main_funestra(MainSetup(projection=projection)))
            arranging, _arranged = self._arrange(
                the_room, _APointer(Frame(carried=(90.0, 0.0))))
            turned.append(arranging.scene_yaw)

        assert turned == pytest.approx([-math.pi / 2, -math.pi / 2])

    def test_a_lift_while_carrying_tilts_the_room_and_remembers_the_tilt_as_carried(self):
        the_room = _a_room_to_arrange()
        the_room.main_unit.verbs.nudge_tilt = lambda degrees: setattr(
            the_room.main_unit.verbs, "tilt_deg", the_room.main_unit.verbs.tilt_deg + degrees)

        _arranging, arranged = self._arrange(the_room, _APointer(Frame(carried=(0.0, 5.0))))

        assert arranged.scene_pitch_deg == 5.0
        the_room.keeper.tilt.assert_called_once_with(5.0, carried=True)

    def test_recentering_turns_the_room_onto_the_wearers_heading(self):
        the_room = _a_room_to_arrange()
        the_room.main_unit.verbs.recenter.ask()

        arranging, _arranged = self._arrange(the_room, views=[_a_view(yaw_deg=30.0)])

        assert arranging.scene_yaw == pytest.approx(yaw_of_orientation(
            (0.0, math.sin(math.radians(15.0)), 0.0, math.cos(math.radians(15.0)))))

    def test_a_layout_reset_puts_every_screen_back_and_forgets_the_layout(self):
        the_room = _a_room_to_arrange()
        the_room.main_unit.verbs.layout_reset.ask()
        pointer = _APointer()

        self._arrange(the_room, pointer)

        assert all(unit.put_back.called for unit in the_room.units)
        the_room.keeper.forget.assert_called_once_with()
        the_room.keeper.settle.assert_called_once_with()
        assert pointer.let_go_asked == 1

    def test_a_frame_that_moves_nothing_puts_nothing_back_and_settles_nothing(self):
        the_room = _a_room_to_arrange()

        self._arrange(the_room)

        assert not any(unit.put_back.called for unit in the_room.units)
        assert not the_room.keeper.settle.called
        assert not the_room.keeper.place.called

    def test_a_dragged_screen_hangs_where_it_was_dragged_and_is_remembered_there(self):
        the_room = _a_room_to_arrange()
        dragged = Placement(azimuth_deg=-50.0, elevation_deg=12.0, width_deg=30.0)

        self._arrange(the_room, _APointer(Frame(moved={LANDSCAPE: dragged}, settled=True)))

        assert the_room.where[LANDSCAPE].placement == dragged
        the_room.keeper.place.assert_called_once_with(LANDSCAPE, dragged)
        the_room.keeper.settle.assert_called_once_with()

    def test_the_sticks_and_the_keyboard_dial_a_wrap_that_can_be_dialed(self):
        the_room = _a_room_to_arrange()
        the_room.main_unit.can_dial_the_wrap = True
        the_room.main_unit.verbs.angle_asked.ask(1.5)

        self._arrange(the_room)

        the_room.main_unit.dial_the_wrap.assert_called_once_with(zoom=1.5, stretch=1.0)

    def test_a_flat_frame_is_not_dialed(self):
        the_room = _a_room_to_arrange()
        the_room.main_unit.verbs.angle_asked.ask(1.5)

        self._arrange(the_room)

        assert not the_room.main_unit.dial_the_wrap.called

    def test_the_main_funestra_is_asked_for_its_reading_every_frame(self):
        the_room = _a_room_to_arrange()

        self._arrange(the_room, now=7.0)

        the_room.main_unit.flash_the_readout.assert_called_once_with(7.0)

    def test_what_the_thumbs_ask_for_is_posted_to_the_dashboard(self):
        the_room = _a_room_to_arrange()

        self._arrange(the_room, hands={"right": HandInput(forward=True)})

        the_room.posts.post.assert_called_once_with((NUDGE_FORWARD,))

    def test_every_unit_is_pointed_at_with_the_frame(self):
        the_room = _a_room_to_arrange()
        frame = Frame(settled=True)

        self._arrange(the_room, _APointer(frame))

        for unit in the_room.units:
            unit.point.assert_called_once_with(frame)

    def test_a_squeeze_brings_forward_what_the_ray_and_the_eyes_both_see(self):
        the_room = _a_room_to_arrange()
        pointer = _APointer(Frame(taken=PORTRAIT))

        arranging, first = self._arrange(the_room, pointer)
        second = arranging.arrange({}, [_a_view()], elapsed_s=1 / 90, now=6.0)

        assert pointer.handed[0] == first.screens
        assert {screen.name for screen in first.screens} == {MAIN, PORTRAIT, LANDSCAPE}
        assert pointer.handed[1] == second.screens
        assert [screen.name for screen in second.screens][-1] == PORTRAIT

    def test_the_eyes_are_handed_the_rooms_hangings_and_the_rotation_it_was_turned_to(self):
        the_room = _a_room_to_arrange()

        _arranging, arranged = self._arrange(the_room, _APointer(Frame(carried=(90.0, 0.0))))

        assert arranged.hangings == room.what_hangs(the_room.units)
        assert arranged.scene_rotation == pytest.approx(
            funestras._scene_rotation(-math.pi / 2, 0.0))

    def test_the_controllers_are_drawn_where_they_are_held(self):
        the_room = _a_room_to_arrange()
        frame = Frame()

        _arranging, arranged = self._arrange(the_room, _APointer(frame))

        the_room.pointing.update.assert_called_once()
        handed_frame, handed_screens, held = the_room.pointing.update.call_args.args
        assert handed_frame is frame
        assert handed_screens == arranged.screens
        assert held == []


def _like(kind, stand_in):
    stand_in.SPOTS = kind.SPOTS
    stand_in.hangings = lambda: kind.hangings(stand_in)
    stand_in.hangs_by = lambda: kind.hangs_by(stand_in)
    stand_in.put_back = lambda: kind.put_back(stand_in)
    return stand_in


_NEVER_DIALED = {"fov_of": lambda _video: None, "height_of": lambda _video: None}


class _VerbsStandIn(SimpleNamespace):
    _video = Path("v.mp4")
    projection = HeadsetVerbs.projection
    wrap = HeadsetVerbs.wrap
    wrap_of = HeadsetVerbs.wrap_of


class _AMainStandIn(SimpleNamespace):
    wraps_the_viewer = _MainUnit.wraps_the_viewer
    texture_on_the_screen = _MainUnit.texture_on_the_screen


@dataclass(frozen=True)
class MainSetup:
    frame: bool = True
    projection: object = FLAT
    showing: bool = False
    flick: bool = True
    flick_projection: object = FLAT
    video: object = None
    fov: object = None
    height: object = None
    verbs: object = None


def _a_main_funestra(setup=None):
    setup = setup or MainSetup()
    return _like(_MainUnit, _AMainStandIn(
        target=SimpleNamespace(ready=setup.frame, aspect=16 / 9, video=setup.video),
        verbs=setup.verbs or _VerbsStandIn(
            projection_of=lambda _video: setup.projection,
            fov_of=lambda _video: setup.fov, height_of=lambda _video: setup.height,
            _video=Path(setup.video or "v.mp4")),
        users_frame_texture=SimpleNamespace(ready=setup.flick, aspect=4 / 3),
        _flick_projection=(Path("flick.mp4"), setup.flick_projection),
        screen=SimpleNamespace(placement=SPOTS[MAIN]),
        owns_the_slot=not setup.showing,
    ))


class TestTheMainFunestrasFrameIsWrappedAsItsOwnVideo:
    WIDE, FLAT_VIDEO = "C:/videos/wide.mp4", "C:/videos/flat.mp4"

    def _showing(self, video):
        return _a_main_funestra(MainSetup(video=video, verbs=_VerbsStandIn(
            projection_of={self.WIDE: EQUIRECT_180_SBS, self.FLAT_VIDEO: FLAT}.get,
            _video=Path(video), **_NEVER_DIALED)))

    def test_a_vr_videos_frame_stays_round_the_viewer_while_a_flat_one_opens(self):
        (hanging,) = self._showing(self.WIDE).hangings()

        assert hanging.wrap == immersive_wrap(EQUIRECT_180_SBS)

    def test_a_flat_videos_frame_stays_on_its_screen_while_a_vr_one_opens(self):
        (hanging,) = self._showing(self.FLAT_VIDEO).hangings()

        assert hanging.wrap is None


class TestDialingTheMainFunestrasWrap:
    VIDEO = "C:/videos/wide.mp4"

    def _main_unit(self, *, projection=FISHEYE_180_SBS, fov=None, height=None, showing=False):
        dialed = {}
        verbs = _VerbsStandIn(
            projection_of=lambda _video: projection,
            fov_of=lambda _video: dialed.get("fov", fov),
            height_of=lambda _video: dialed.get("height", height),
            set_fov=lambda degrees: dialed.update(fov=degrees),
            set_height=lambda value: dialed.update(height=value),
            _video=Path(self.VIDEO),
        )
        main_unit = _a_main_funestra(MainSetup(video=self.VIDEO, verbs=verbs, showing=showing))
        main_unit.target.aspect = 1.0
        main_unit.dial_the_wrap = lambda **dials: _MainUnit.dial_the_wrap(main_unit, **dials)
        return main_unit, dialed

    def test_a_frame_is_drawn_at_the_field_of_view_and_height_dialed_for_its_video(self):
        main_unit, _ = self._main_unit(fov=140.0, height=1.2)

        (hanging,) = main_unit.hangings()

        assert hanging.wrap == immersive_wrap(FISHEYE_180_SBS, fov_deg=140.0, height=1.2)

    def test_a_wrapped_frame_can_be_dialed_but_never_a_flat_one_or_the_360(self):
        for projection, dialed in ((FISHEYE_180_SBS, True), (EQUIRECT_180_SBS, True),
                                   (RECTILINEAR_SBS, True), (EQUIRECT_360, False), (FLAT, False)):
            main_unit, _ = self._main_unit(projection=projection)

            assert _MainUnit.can_dial_the_wrap.fget(main_unit) is dialed, projection

    def test_the_frame_is_left_alone_while_a_flick_holds_the_slot(self):
        main_unit, _ = self._main_unit(showing=True)

        assert _MainUnit.can_dial_the_wrap.fget(main_unit) is False

    def test_a_zoom_scales_the_field_of_view_it_has_now_and_a_stretch_the_height(self):
        main_unit, dialed = self._main_unit(fov=100.0, height=1.5)

        main_unit.dial_the_wrap(zoom=1.1, stretch=0.8)

        assert dialed == {"fov": pytest.approx(110.0), "height": pytest.approx(1.2)}

    def test_a_video_never_dialed_starts_from_what_its_projection_carries(self):
        main_unit, dialed = self._main_unit()

        main_unit.dial_the_wrap(zoom=0.5, stretch=2.0)

        assert dialed == {"fov": pytest.approx(90.0), "height": pytest.approx(2.0)}

    def test_a_frame_that_zooms_nothing_and_stretches_nothing_dials_nothing(self):
        main_unit, dialed = self._main_unit()

        main_unit.dial_the_wrap(zoom=1.0, stretch=1.0)

        assert dialed == {}


class TestTheReadoutOfAWrappedFrame:
    VIDEO = "C:/videos/wide.mp4"

    def _main_unit(self, *, projection=FISHEYE_180_SBS, fov=None, height=None, showing=False):
        return _a_main_funestra(MainSetup(video=self.VIDEO, projection=projection, fov=fov, height=height,
                            showing=showing))

    def test_the_main_funestra_reads_its_own_wrap(self):
        unit = self._main_unit(fov=158.4, height=1.2)

        assert _MainUnit.wrap_readout.fget(unit) == "Fisheye · 158° · height 1.20"

    def test_it_reads_nothing_while_a_flick_holds_the_slot(self):
        assert _MainUnit.wrap_readout.fget(self._main_unit(showing=True)) is None

    def test_each_new_reading_flashes_over_the_main_screen_and_a_repeat_does_not(self):
        flashed = []
        unit = SimpleNamespace(
            _readout=WrapReadout(), screen_name=MAIN, wrap_readout="Fisheye · 158° · height 1.20",
            _messages=SimpleNamespace(flash=lambda said, **how: flashed.append((said, how))))

        _MainUnit.flash_the_readout(unit, 1.0)
        _MainUnit.flash_the_readout(unit, 2.0)

        assert flashed == [("Fisheye · 158° · height 1.20",
                            {"level": MESSAGE, "screen": MAIN, "now": 1.0})]


class TestTheBannerOverAWrappedFrame:
    def _main_unit(self, *, wraps):
        return SimpleNamespace(
            _messages=SimpleNamespace(flashed=lambda _screen: "a banner"), wraps_the_viewer=wraps,
            screen_name=MAIN)

    def test_a_flat_frame_takes_its_banner_into_itself(self):
        assert _MainUnit.message_into_the_frame(self._main_unit(wraps=False)) == "a banner"

    def test_a_wrapped_frame_takes_none_into_itself(self):
        assert _MainUnit.message_into_the_frame(self._main_unit(wraps=True)) is None

    def _floating(self, *, wraps, flashed):
        unit = SimpleNamespace(
            _main_unit=SimpleNamespace(wraps_the_viewer=wraps),
            _messages=SimpleNamespace(flashed=lambda _screen: flashed),
            _lock=threading.Lock(), _image=None, _uploaded=None, _painted=None,
            texture=SimpleNamespace(ready=True, texture=object(), aspect=6.0),
            screen=SimpleNamespace(placement=Placement(0.0, 24.0, 1.0)),
        )
        _MessageUnit.pump(unit, None, now=0.0)
        return unit

    def test_it_floats_the_banner_in_front_while_the_frame_wraps_the_viewer(self):
        flash_message = SimpleNamespace(message="Fisheye · 158° · height 1.20", level=MESSAGE)

        unit = self._floating(wraps=True, flashed=flash_message)

        (hanging,) = _MessageUnit.hangings(unit)
        assert hanging.in_front
        assert hanging.blend
        assert hanging.screen.name == MESSAGE_SCREEN
        assert not hanging.screen.pressable
        assert not hanging.screen.movable
        assert unit._image is not None
        assert unit.screen.placement.width_deg == pytest.approx(
            unit._image.width / funestras.MESSAGE_PX_PER_DEG)

    def test_it_hangs_nothing_over_a_flat_frame_which_draws_its_own(self):
        flash_message = SimpleNamespace(message="Flat", level=MESSAGE)

        assert _MessageUnit.hangings(self._floating(wraps=False, flashed=flash_message)) == ()

    def test_it_hangs_nothing_once_the_banner_has_faded(self):
        unit = self._floating(wraps=True, flashed=SimpleNamespace(message="Flat", level=MESSAGE))

        unit._messages = SimpleNamespace(flashed=lambda _screen: None)
        _MessageUnit.pump(unit, None, now=3.0)

        assert _MessageUnit.hangings(unit) == ()


class TestTheMainFunestraUnderThePointer:
    def _units(self, setup=None):
        return (_a_main_funestra(setup),)

    def _slot(self, setup=None):
        hangings = room.what_hangs(self._units(setup))
        return hangings[0].screen if hangings else None

    def test_the_video_has_the_slot_while_the_funestra_shows_kino(self):
        assert _MainUnit.owns_the_slot.fget(
            SimpleNamespace(funestra=SimpleNamespace(showing=MainMode.KINO)))
        assert not _MainUnit.owns_the_slot.fget(
            SimpleNamespace(funestra=SimpleNamespace(showing=MainMode.GENAU)))

    def test_the_primary_offers_both_handles(self):
        screen = self._slot()

        assert (screen.name, screen.movable, screen.resizable) == (MAIN, True, True)
        assert screen.placement == SPOTS[MAIN]
        assert screen.aspect == 16 / 9

    def test_a_flat_main_funestra_is_a_frame_a_squeeze_clicks_or_carries(self):
        assert self._slot().item

    def test_genaus_flick_is_what_the_pointer_finds_there_while_it_has_the_scene(self):
        screen = self._slot(MainSetup(showing=True))

        assert screen.name == MAIN
        assert screen.aspect == 4 / 3

    def test_genaus_flick_is_offered_at_the_width_it_hangs_at_to_keep_the_slots_area(self):
        screen = self._slot(MainSetup(showing=True))

        assert screen.placement == shown_at(MAIN, SPOTS[MAIN], 4 / 3)
        assert widened(SPOTS[MAIN], screen.widened_by) == screen.placement

    @pytest.mark.parametrize("state", [
        MainSetup(projection=EQUIRECT_180_SBS),
        MainSetup(showing=True, flick_projection=EQUIRECT_180_SBS),
    ])
    def test_a_wrapped_video_has_no_edges_to_take_hold_of(self, state):
        screen = self._slot(state)

        assert (screen.movable, screen.resizable) == (False, False)
        assert (screen.pressable, screen.immersive) == (True, True)

    def test_a_wrapped_video_goes_round_the_viewer_rather_than_on_a_quad(self):
        (wrap,) = room.what_hangs(self._units(MainSetup(projection=EQUIRECT_180_SBS)))

        assert wrap.mesh is None
        assert wrap.wrap == immersive_wrap(EQUIRECT_180_SBS)

    @pytest.mark.parametrize("state", [
        MainSetup(frame=False),
        MainSetup(showing=True, flick=False),
    ])
    def test_a_slot_with_no_frame_in_it_is_not_in_the_scene_at_all(self, state):
        assert self._slot(state) is None

    def test_a_drag_on_the_slot_moves_genaus_frame_with_the_video(self):
        main_unit = _a_main_funestra()

        room.where_they_hang([main_unit])[MAIN].put(_UNDER_THE_DASH)

        assert main_unit.screen.placement == _UNDER_THE_DASH
        assert self._slot(MainSetup(showing=True)).placement == shown_at(MAIN, SPOTS[MAIN], 4 / 3)

    def _the_room_around_the_slot(self) -> list:
        dash = _like(_DashUnit, SimpleNamespace(
            texture=SimpleNamespace(ready=False, aspect=2.5),
            screen=SimpleNamespace(placement=SPOTS[DASH])))
        reference = _like(_ReferenceUnit, SimpleNamespace(
            showing=False, texture=SimpleNamespace(ready=False, aspect=1.7),
            screen=SimpleNamespace(placement=_UNDER_THE_DASH)))
        return room.arranged(Stacking(), room.what_hangs(
            [*self._units(), _a_satellite(LANDSCAPE), _a_main_hud(), dash, reference]))

    def test_the_main_funestra_is_listed_under_the_screens_that_overlap_it(self):
        screens = self._the_room_around_the_slot()

        assert [screen.name for screen in screens] == [MAIN, LANDSCAPE, MAIN_HUD]
        main_hud_unit = screens[-1]
        assert (main_hud_unit.pressable, main_hud_unit.movable, main_hud_unit.resizable) == (True, False, False)

    def test_a_satellite_is_a_frame_and_the_main_hud_is_not(self):
        screens = {screen.name: screen for screen in self._the_room_around_the_slot()}

        assert screens[LANDSCAPE].item
        assert not screens[MAIN_HUD].item


class TestGenausFrameInTheSlot:
    def _unit(self, *, showing=True, vr_dirs=()):
        unit = _MainUnit.__new__(_MainUnit)
        unit.funestra = SimpleNamespace(showing=MainMode.GENAU if showing else MainMode.KINO)
        unit.users_frames = LatestFrame()
        unit.users_frame_texture = _FakeTexture()
        unit._uploaded_frame = None
        unit._flick_projection = (None, "")
        unit._vr_dirs = vr_dirs
        unit.screen = SimpleNamespace(placement=SPOTS[MAIN],
                                      rehang_at=lambda _placement, _aspect: None)
        return unit

    def _handed(self, unit, flick: Path):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        unit.users_frames.show(FlickFrame(frame=frame, flick=flick), (640, 360))
        unit._show_the_users_frame()
        return frame

    def test_the_frame_is_uploaded_untouched(self):
        unit = self._unit()

        frame = self._handed(unit, Path("C:/flicks/VR/alpha_180.mp4"))

        assert unit.users_frame_texture.uploads == [frame]

    def test_a_frame_is_uploaded_once_however_many_frames_the_loop_runs(self):
        unit = self._unit()

        self._handed(unit, Path("C:/flicks/VR/alpha_180.mp4"))
        unit._show_the_users_frame()

        assert len(unit.users_frame_texture.uploads) == 1

    def test_a_flick_in_the_vr_folder_wraps_the_viewer_and_a_flat_one_hangs_on_the_screen(self):
        vr = Path("C:/flicks/VR")
        unit = self._unit(vr_dirs=(vr,))

        self._handed(unit, vr / "scene one.mp4")
        assert unit._flick_projection[1] == EQUIRECT_180_SBS
        assert unit.hangings()[0].wrap == immersive_wrap(EQUIRECT_180_SBS)

        self._handed(unit, Path("C:/flicks/2D/scene two.mp4"))
        assert unit._flick_projection[1] == FLAT
        assert unit.hangings()[0].wrap is None

    def test_nothing_is_uploaded_while_the_video_has_the_slot(self):
        unit = self._unit(showing=False)

        self._handed(unit, Path("C:/flicks/VR/alpha_180.mp4"))

        assert unit.users_frame_texture.uploads == []

    def test_nothing_hangs_before_a_frame_has_arrived(self):
        unit = self._unit()

        unit._show_the_users_frame()

        assert unit.hangings() == ()


class _FakeRenderer:
    def __init__(self) -> None:
        self.screens: list[str] = []

    def begin_eye(self) -> None:
        pass

    def draw_screen(self, mesh, _texture, _view_proj, blend: bool = False) -> None:
        self.screens.append(mesh)

    def draw_immersive(self, *_args) -> None:
        self.screens.append("immersive")


def _hanging(name: str, aspect: float):
    return SimpleNamespace(
        texture=SimpleNamespace(ready=True, texture=object(), aspect=aspect),
        screen=SimpleNamespace(ready=True, mesh=name,
                               placement=SPOTS.get(name, _UNDER_THE_DASH)),
    )


class TestEveryHangingScreenIsDrawn:
    def _views(self):
        fov = SimpleNamespace(angle_left=-0.8, angle_right=0.8,
                              angle_up=0.8, angle_down=-0.8)
        pose = SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
        )
        return [SimpleNamespace(fov=fov, pose=pose)]

    def _draw(self, *, showing: bool = False, browsing: bool = False, projection=None,
              stacking=None):
        renderer = _FakeRenderer()
        session = SimpleNamespace(
            bind_eye_framebuffer=lambda _i: None, release_eye_framebuffer=lambda _i: None)
        main_unit = _a_main_funestra(MainSetup(frame=projection is not None, projection=projection or FLAT,
                                 flick=False))
        main_unit.target.texture = object()
        main_unit.screen = SimpleNamespace(ready=True, mesh=MAIN, placement=SPOTS[MAIN])
        main_hud_unit = _like(_MainHudUnit, _hanging(MAIN_HUD, 1.2))
        main_hud_unit._buttons, main_hud_unit._shown = _buttons_not_hanging_names(MAIN), True
        dash = _like(_DashUnit, _hanging(DASH, 2.5))
        reference = _like(_ReferenceUnit, _hanging(REFERENCE, 1.7))
        reference.showing = showing
        library = _like(_LibraryUnit, _hanging(LIBRARY, 16 / 9))
        library.showing = browsing
        hangings = room.what_hangs([main_unit, main_hud_unit, dash, reference, library])
        screens = room.arranged(stacking or Stacking(), hangings)
        draw_eyes(
            Headset(session, renderer, None),
            Arranged(hangings, screens, np.eye(4, dtype=np.float64), 0.0, 0.0),
            SimpleNamespace(draw=lambda *_a: None), self._views(), as_quads=set(),
        )
        return renderer

    def test_the_main_hud_reaches_the_eyes(self):
        assert "panel" in self._draw().screens

    @pytest.mark.parametrize("projection", [None, FLAT, EQUIRECT_180_SBS])
    def test_the_dashboard_reaches_them_whichever_video_is_in_the_slot(self, projection):
        assert "dash" in self._draw(projection=projection).screens

    def test_the_reference_reaches_them_while_it_is_up(self):
        assert "reference" in self._draw(showing=True).screens

    def test_and_stays_out_of_them_while_it_is_down(self):
        assert "reference" not in self._draw(showing=False).screens

    def test_the_browse_reaches_them_while_it_is_up(self):
        assert LIBRARY in self._draw(browsing=True).screens

    def test_the_browse_stays_out_of_them_while_it_is_put_away(self):
        assert LIBRARY not in self._draw(browsing=False).screens

    def test_the_browse_is_drawn_in_front_of_whatever_was_taken_last(self):
        stacking = Stacking()
        stacking.take(MAIN_HUD)

        drawn = self._draw(projection=FLAT, browsing=True, stacking=stacking).screens

        assert drawn[-1] == LIBRARY

    def test_they_are_drawn_back_to_front_as_they_stand(self):
        stacking = Stacking()
        stacking.take(MAIN_HUD)

        drawn = self._draw(projection=FLAT, stacking=stacking).screens

        assert drawn.index(MAIN) < drawn.index(DASH) < drawn.index(MAIN_HUD)

    def test_a_video_wrapped_round_the_viewer_is_drawn_first_however_it_was_taken(self):
        stacking = Stacking()
        stacking.take(MAIN)

        drawn = self._draw(projection=EQUIRECT_180_SBS, stacking=stacking).screens

        assert drawn[0] == "immersive"
        assert sorted(drawn[1:]) == sorted([MAIN_HUD, DASH])


_UNDER_THE_DASH = Placement(azimuth_deg=0.0, elevation_deg=38.0, width_deg=54.0)

class _FakeChromeRenderer:
    def __init__(self) -> None:
        self.drawn: list[str] = []

    def draw_solid(self, *_args) -> None:
        self.drawn.append("flat")

    def draw_shaded(self, *_args) -> None:
        self.drawn.append("solid")


_IN_HAND = (np.array([0.2, -0.3, -0.4]), np.eye(3))


class TestWhatThePointerDraws:
    def _drawn(self, *updates) -> list[str]:
        renderer = _FakeChromeRenderer()
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            pointing = _PointerDrawing()
            for frame, held in updates:
                pointing.update(frame, [], held)
            pointing.draw(renderer, np.eye(4, dtype=np.float32))
        return renderer.drawn

    def test_the_controller_is_drawn_over_the_laser_that_leaves_it(self):
        laser = Ray(origin=(0.2, -0.3, -0.4), direction=(0.0, 0.0, -1.0))

        assert self._drawn((Frame(ray=laser), [_IN_HAND])) == ["flat", "solid"]

    def test_a_controller_that_loses_tracking_stops_being_drawn(self):
        assert self._drawn((Frame(), [_IN_HAND]), (Frame(), [])) == []


def _buttons_not_hanging_names(funestra: str) -> HudButtons:
    return HudButtons(funestra, post=lambda _command: None, texture=object, mesh=object)


def _a_main_hud(*, ready=True):
    return _like(_MainHudUnit, SimpleNamespace(
        texture=SimpleNamespace(ready=ready, aspect=1.2),
        screen=SimpleNamespace(placement=SPOTS[MAIN_HUD]),
        _buttons=_buttons_not_hanging_names(MAIN), _shown=True,
    ))


def _slot(*, wrapped=False):
    return _a_main_funestra(MainSetup(projection=EQUIRECT_180_SBS if wrapped else FLAT))


def _a_dash(tmp_path, *, wrapped=False, texture=None, remembered=None):
    with patch("fun_time_vr.funestras.FrameTexture"):
        dash = _DashUnit(
            _slot(wrapped=wrapped),
            SimpleNamespace(dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
                            dashboard_state_file=tmp_path / "dashboard_state.ini",
                            state_dir=tmp_path),
            remembered=remembered or {},
            messages=MessageBoard(tmp_path / "event_log.jsonl"),
            reference_flag=tmp_path / REFERENCE_OPEN_FILENAME,
        )
    if texture is not None:
        dash.texture = texture
    return dash


class TestTheDashUnderThePointer:
    def _unit(self, tmp_path):
        return _a_dash(tmp_path)

    @staticmethod
    def _uv_of(action: str) -> tuple[float, float]:
        width, height = DASH_WIDTH_PX, dash_height()
        rect = dash_actions()[action]
        return ((rect.x + rect.width // 2 + 0.5) / width,
                1 - (rect.y + rect.height // 2 + 0.5) / height)

    def test_a_press_on_a_control_posts_its_command(self, tmp_path):
        unit = self._unit(tmp_path)

        unit.point(Frame(events=[PressEvent(PRESS, DASH, *self._uv_of(QUIT_BUTTON))]))
        unit.pump(threading.Event(), 0.0)

        posted = (tmp_path / "dashboard_cmd.txt").read_text(encoding="utf-8")
        assert posted.strip() == QUIT_BUTTON

    def test_the_question_mark_lights_while_the_session_has_the_reference_up(self, tmp_path):
        unit = self._unit(tmp_path)
        write_flag(tmp_path / REFERENCE_OPEN_FILENAME, True)

        unit.pump(threading.Event(), 0.0)

        rect = dash_actions()[HELP_REFERENCE]
        assert unit._image.getpixel((rect.x, rect.y + rect.height // 2))[:3] == BLUE

    def test_a_press_meant_for_another_screen_is_not_its(self, tmp_path):
        unit = self._unit(tmp_path)

        unit.point(Frame(events=[PressEvent(PRESS, MAIN_HUD, *self._uv_of(QUIT_BUTTON))]))
        unit.pump(threading.Event(), 0.0)

        assert not (tmp_path / "dashboard_cmd.txt").exists()


class TestTheOsr2PartOnTheDash:
    @staticmethod
    def _uv(x: int, y: int) -> tuple[float, float]:
        place = osr2_part_rect()
        return ((place.x + x + 0.5) / DASH_WIDTH_PX, 1 - (place.y + y + 0.5) / dash_height())

    @staticmethod
    def _in_its_room(image) -> np.ndarray:
        place = osr2_part_rect()
        return np.asarray(image)[place.y:place.y + place.height, place.x:place.x + place.width]

    @staticmethod
    def _pumped(unit) -> None:
        unit.pump(threading.Event(), 0.0)

    def test_it_draws_the_part_the_room_publishes_with_its_holders_readout(self, tmp_path):
        unit = _a_dash(tmp_path)
        part = published(tmp_path, a_part(FunTimeFunestra.PORTRAIT))
        readout = handed_on(tmp_path, FunTimeFunestra.PORTRAIT, a_readout())

        self._pumped(unit)

        drawn = with_the_osr2_part(paint_dash(DashState(), []), Osr2ControlsHost(post=[].append).drawing(
            MainHud(main_hud_model=part, drive=readout)))
        assert np.array_equal(self._in_its_room(unit._image), self._in_its_room(drawn))

    def test_until_the_room_publishes_one_the_room_kept_for_it_stays_empty(self, tmp_path):
        unit = _a_dash(tmp_path)

        self._pumped(unit)

        assert np.array_equal(self._in_its_room(unit._image),
                              self._in_its_room(paint_dash(DashState(), [])))

    def test_each_pump_draws_it_afresh_while_one_is_up_for_the_dot_walks_by_the_clock(
            self, tmp_path, monkeypatch):
        unit = _a_dash(tmp_path)
        published(tmp_path, a_part())
        asked = []
        draw = unit._pointer.part.drawing
        monkeypatch.setattr(unit._pointer.part, "drawing",
                            lambda hud: asked.append(hud) or draw(hud))

        self._pumped(unit)
        self._pumped(unit)

        assert len(asked) == 2

    def test_it_hangs_a_new_frame_only_once_what_it_draws_has_changed(self, tmp_path):
        unit = _a_dash(tmp_path)
        published(tmp_path, a_part())
        self._pumped(unit)
        first = unit._image

        self._pumped(unit)
        unchanged = unit._image
        handed_on(tmp_path, FunTimeFunestra.MAIN, a_readout())
        self._pumped(unit)

        assert unchanged is first
        assert unit._image is not first

    def test_a_part_that_changes_on_its_own_leaves_the_bar_and_the_log_as_painted(
            self, tmp_path, monkeypatch):
        unit = _a_dash(tmp_path)
        published(tmp_path, a_part())
        painted = []
        paint = funestras.paint_dash
        monkeypatch.setattr(funestras, "paint_dash",
                            lambda *drawn, **shown: painted.append(drawn) or paint(*drawn, **shown))

        self._pumped(unit)
        handed_on(tmp_path, FunTimeFunestra.MAIN, a_readout())
        self._pumped(unit)

        assert len(painted) == 1

    def test_a_press_on_one_of_its_buttons_posts_where_the_bars_presses_go(self, tmp_path):
        unit = _a_dash(tmp_path)
        part = published(tmp_path, a_part())
        self._pumped(unit)

        unit.point(Frame(events=[
            PressEvent(PRESS, DASH, *self._uv(*center_of(part, "broker_panel")))]))
        self._pumped(unit)

        posted = (tmp_path / "dashboard_cmd.txt").read_text(encoding="utf-8")
        assert posted.split() == ["broker_panel"]

    def test_a_drag_along_a_band_of_its_readout_goes_on_setting_it_until_let_go(self, tmp_path):
        unit = _a_dash(tmp_path)
        published(tmp_path, a_part())
        handed_on(tmp_path, FunTimeFunestra.MAIN, a_readout())
        self._pumped(unit)
        x, y, width, height = band_of(a_part(), a_readout(), "speed")
        middle = y + height // 2

        unit.point(Frame(events=[
            PressEvent(PRESS, DASH, *self._uv(x + 2, middle)),
            PressEvent(DRAG, DASH, *self._uv(x + width - 2, middle)),
            PressEvent(RELEASE, DASH),
            PressEvent(DRAG, DASH, *self._uv(x + width // 2, middle)),
        ]))
        self._pumped(unit)

        posted = (tmp_path / "dashboard_cmd.txt").read_text(encoding="utf-8").split()
        assert [command.rsplit("_", 1)[0] for command in posted] == ["robot_hand_speed"] * 2
        assert posted[0] != posted[1]

    def test_the_ray_resting_on_one_of_its_buttons_lights_it(self, tmp_path):
        unit = _a_dash(tmp_path)
        part = published(tmp_path, a_part())
        self._pumped(unit)
        unlit = self._in_its_room(unit._image).copy()
        x, y = center_of(part, "broker_panel")

        unit.point(Frame(hover=Hover(DASH, SURFACE, *self._uv(x, y))))
        self._pumped(unit)

        lit = self._in_its_room(unit._image)
        assert not np.array_equal(lit[y - 4:y + 4, x - 4:x + 4], unlit[y - 4:y + 4, x - 4:x + 4])


class _FakeLibraryHost:
    def __init__(self):
        self.sent: list[str] = []
        self.said: list[str] = []
        self.frames: dict[int, tuple[int, int, bytes]] = {}
        self.closed = False

    def send(self, line):
        self.sent.append(line)

    def answers(self):
        said, self.said = self.said, []
        return said

    def frame(self, token):
        return self.frames.pop(token, None)

    def close(self):
        self.closed = True


def _a_library(tmp_path, host, remembered=None, metadata_root=None, genau_has_the_slot=False):
    with patch("fun_time_vr.funestras.FrameTexture"):
        manifest = SimpleNamespace(
            commands=SimpleNamespace(
                main_funestra_cmd_file=tmp_path / "main_player_cmd.txt",
                main_funestra_status_file=tmp_path / "main_player_status.txt",
                genau_cmd_file=tmp_path / "genau_cmd.txt",
                genau_status_file=tmp_path / "genau_status.txt",
                dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            ),
            regen=SimpleNamespace(metadata_root="" if metadata_root is None else str(metadata_root)),
        )
        return _LibraryUnit(
            manifest,
            remembered=remembered or {},
            flag=tmp_path / LIBRARY_OPEN_FILENAME,
            host=host,
            genau_has_the_slot=lambda: genau_has_the_slot,
        )


def _a_frame(value: int = 7) -> tuple[int, int, bytes]:
    width, height = LIBRARY_SIZE_PX
    return width, height, bytes([value]) * (width * height * 4)


def _uv(x: int, y: int) -> tuple[float, float]:
    width, height = LIBRARY_SIZE_PX
    return (x + 0.5) / width, 1 - (y + 0.5) / height


def _no_gallery():
    return SimpleNamespace(takes_the_stick=False)


class TestTheLibraryUnderThePointer:
    def test_it_opens_where_the_last_session_left_it(self, tmp_path):
        moved = Placement(azimuth_deg=-25.0, elevation_deg=18.0, width_deg=60.0)

        unit = _a_library(tmp_path, _FakeLibraryHost(), remembered={LIBRARY: moved})

        assert unit.screen.placement == moved

    def test_opening_it_asks_the_browser_to_open_on_the_video_playing(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        (tmp_path / "main_player_status.txt").write_text(
            "video=C:/videos/Scene One.mp4\n", encoding="utf-8")
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)

        unit.pump(threading.Event(), 0.0)
        unit.pump(threading.Event(), 0.0)

        assert host.sent == ["open 1 C:/videos/Scene One.mp4"]

    def test_a_press_on_it_reaches_the_browser_at_the_point_pressed(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        host.sent.clear()

        unit.point(Frame(events=(PressEvent(PRESS, LIBRARY, *_uv(100, 200)),)))
        unit.point(Frame(events=(PressEvent(DRAG, LIBRARY, *_uv(110, 260)),)))
        unit.point(Frame(events=(PressEvent(RELEASE, LIBRARY),)))
        unit.pump(threading.Event(), 0.0)

        assert host.sent == ["press 100 200", "drag 110 260", "release"]

    def test_a_video_picked_plays_on_the_main_funestra_and_puts_the_browse_away(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)

        host.said.append("picked C:/videos/Scene One.mp4")
        unit.pump(threading.Event(), 0.0)

        assert (tmp_path / "main_player_cmd.txt").read_text(encoding="utf-8").strip() == (
            f"PLAY_FILE {Path('C:/videos/Scene One.mp4')}")
        assert (tmp_path / "dashboard_cmd.txt").read_text(encoding="utf-8").strip() == (
            BROWSE_LIBRARY_CLOSE)
        assert not unit.showing

    def test_with_genau_in_the_main_funestra_it_opens_genaus_flicks_on_the_flick_up(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host, genau_has_the_slot=True)
        (tmp_path / "genau_status.txt").write_text(
            "flick=C:/flicks/Loop One.mp4\n", encoding="utf-8")
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)

        unit.pump(threading.Event(), 0.0)

        assert host.sent == ["flicks 1 C:/flicks/Loop One.mp4"]

    def test_a_flick_picked_plays_on_genau_and_puts_the_browse_away(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host, genau_has_the_slot=True)
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)

        host.said.append("picked C:/flicks/Loop Two.mp4")
        unit.pump(threading.Event(), 0.0)

        assert (tmp_path / "genau_cmd.txt").read_text(encoding="utf-8").strip() == (
            f"PLAY_FILE {Path('C:/flicks/Loop Two.mp4')}")
        assert not (tmp_path / "main_player_cmd.txt").exists()
        assert not unit.showing

    def test_a_vr_video_picked_plays_with_the_script_in_the_librarys_own_tree(self, tmp_path):
        library = tmp_path / "library" / "videos"
        kept = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished" / "Scene One.mp4"
        script = library / "scripts" / "scripts" / "VR" / "finished" / "Scene One.funscript"
        script.parent.mkdir(parents=True)
        script.write_text("{}", encoding="utf-8")
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host, metadata_root=library / "metadata")
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)

        host.said.append(f"picked {kept}")
        unit.pump(threading.Event(), 0.0)

        assert str(script) in (tmp_path / "main_player_cmd.txt").read_text(encoding="utf-8")

    def test_its_own_close_puts_it_away_playing_nothing(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)

        host.said.append("dismissed")
        unit.pump(threading.Event(), 0.0)

        assert not (tmp_path / "main_player_cmd.txt").exists()
        assert (tmp_path / "dashboard_cmd.txt").read_text(encoding="utf-8").strip() == (
            BROWSE_LIBRARY_CLOSE)
        assert not unit.showing

    def test_the_browse_reaches_the_headset_only_while_it_is_up(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        unit.texture = _FakeMainHudTexture()
        host.frames[1] = _a_frame()
        unit.pump(threading.Event(), 0.0)
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        assert not hasattr(unit.texture, "uploaded")

        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()

        assert unit.showing
        assert unit.texture.uploaded.shape == (LIBRARY_SIZE_PX[1], LIBRARY_SIZE_PX[0], 4)
        assert unit.texture.uploaded.max() == 7

    def test_a_browse_just_opened_shows_nothing_left_from_the_last_until_it_draws(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)

        unit.pump(threading.Event(), 0.0)
        assert not unit.showing

        unit.pump(threading.Event(), 1.0)
        assert unit.showing

    def test_the_stick_scrolls_it_while_the_pointer_is_on_it(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        host.frames[1] = _a_frame()
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        unit.point(Frame(hover=Hover(LIBRARY, SURFACE, *_uv(300, 400))))
        host.sent.clear()

        assert unit.takes_the_stick
        unit.scroll(90.0)
        unit.scroll(90.0)
        unit.pump(threading.Event(), 0.0)

        assert host.sent == ["hover 300 400", "scroll 180"]

    def test_the_stick_is_left_alone_while_the_pointer_is_elsewhere(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        host.frames[1] = _a_frame()
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        assert unit.showing

        unit.point(Frame(hover=Hover(MAIN, SURFACE, 0.5, 0.5)))

        assert not unit.takes_the_stick

    def test_the_stick_on_it_scrolls_it_and_sizes_no_funestra(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        host.frames[1] = _a_frame()
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        unit.point(Frame(hover=Hover(LIBRARY, SURFACE, *_uv(300, 400))))
        host.sent.clear()

        hands = _hands_for_the_funestras(
            unit, _no_gallery(),
            {"left": HandInput(stick_y=0.2),
             "right": HandInput(stick_y=-1.0, forward=True)},
            elapsed_s=0.5)
        unit.pump(threading.Event(), 0.0)

        assert hands == {"left": HandInput(), "right": HandInput(forward=True)}
        assert host.sent[-1] == scroll_line(int(scroll_from_stick(-1.0, 0.5)))

    def test_the_stick_on_it_steps_no_projection_either(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        host.frames[1] = _a_frame()
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        unit.point(Frame(hover=Hover(LIBRARY, SURFACE, *_uv(300, 400))))

        hands = _hands_for_the_funestras(
            unit, _no_gallery(), {"right": HandInput(stick_x=0.9)}, elapsed_s=0.5)

        assert hands == {"right": HandInput()}

    def test_the_stick_elsewhere_is_the_funestras_to_size(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        hands = {"right": HandInput(stick_y=-1.0)}

        assert _hands_for_the_funestras(
            unit, _no_gallery(), hands, elapsed_s=0.5) == hands
        assert host.sent == []

    def test_closing_the_session_ends_its_browser(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)

        unit.close()

        assert host.closed


class TestWhatThePointerCanReach:
    def _screens(self, tmp_path, *, reference_showing=False, wrapped=False):
        main_hud_unit = _a_main_hud()
        dash = _a_dash(tmp_path, wrapped=wrapped,
                       texture=SimpleNamespace(ready=True, aspect=2.5))
        main_unit = _slot(wrapped=wrapped)
        main_unit.target = SimpleNamespace(ready=False, aspect=16 / 9, video=None)
        reference = _like(_ReferenceUnit, SimpleNamespace(
            showing=reference_showing,
            texture=SimpleNamespace(ready=True, aspect=1.7),
            screen=SimpleNamespace(placement=_UNDER_THE_DASH),
        ))
        return {s.name: s for s in room.arranged(Stacking(), room.what_hangs(
            [main_unit, main_hud_unit, dash, reference]))}

    def test_the_dash_is_one_of_them(self, tmp_path):
        assert DASH in self._screens(tmp_path)

    def test_the_reference_joins_them_only_while_it_is_up(self, tmp_path):
        assert REFERENCE not in self._screens(tmp_path)
        assert REFERENCE in self._screens(tmp_path, reference_showing=True)

    def test_it_can_be_pressed_and_dragged(self, tmp_path):
        screens = self._screens(tmp_path)

        assert screens[DASH].pressable
        assert screens[DASH].movable
        assert screens[MAIN_HUD].pressable
        assert not screens[MAIN_HUD].movable

    def test_the_reference_is_pressed_but_offers_no_handles_of_its_own(self, tmp_path):
        reference = self._screens(tmp_path, reference_showing=True)[REFERENCE]

        assert reference.pressable
        assert not reference.movable
        assert not reference.resizable

    def test_the_dashboard_keeps_the_only_handle_when_it_carries_the_main_hud(self, tmp_path):
        screens = self._screens(tmp_path, wrapped=True)

        assert screens[DASH].movable
        assert not screens[DASH].resizable
        assert screens[MAIN_HUD].pressable
        assert not screens[MAIN_HUD].movable


def _a_satellite(funestra: str, *, hud: bool = False):
    placement = SPOTS[funestra]
    return _like(_SatelliteUnit, SimpleNamespace(
        screen_name=funestra, target=SimpleNamespace(ready=True, aspect=16 / 9),
        screen=SimpleNamespace(placement=placement), hud_ready=hud,
        hud_screen=SimpleNamespace(placement=attached_below(
            placement, aspect=16 / 9, shape=Footprint(20.0, 6.0))),
        hud_texture=SimpleNamespace(aspect=6.0),
        _buttons=_buttons_not_hanging_names(funestra),
    ))


@dataclass(frozen=True)
class RoomExtras:
    main_hud_unit: object = None
    dash: object = None
    reference_up: bool = False
    wrapped: bool = False


class TestWhatComesForwardTogether:
    def _arranged(self, stacking, satellites, extras=None):
        extras = extras or RoomExtras()
        dash = extras.dash or _like(_DashUnit, SimpleNamespace(
            texture=SimpleNamespace(ready=False, aspect=2.5),
            screen=SimpleNamespace(placement=SPOTS[DASH])))
        reference = _like(_ReferenceUnit, SimpleNamespace(
            showing=extras.reference_up, texture=SimpleNamespace(ready=True, aspect=1.7),
            screen=SimpleNamespace(placement=_UNDER_THE_DASH)))
        return [screen.name for screen in room.arranged(stacking, room.what_hangs(
            [_slot(wrapped=extras.wrapped), *satellites,
             extras.main_hud_unit or _a_main_hud(ready=False), dash, reference]))]

    def test_the_reference_comes_forward_as_part_of_the_dashboard(self):
        stacking = Stacking()
        dash = _like(_DashUnit, SimpleNamespace(
            texture=SimpleNamespace(ready=True, aspect=2.5),
            screen=SimpleNamespace(placement=SPOTS[DASH])))
        stacking.take(MAIN)
        stacking.take(REFERENCE)

        assert self._arranged(stacking, [], RoomExtras(dash=dash, reference_up=True)) == [
            MAIN, DASH, REFERENCE]

    def test_a_satellites_hud_brings_its_frame_forward_with_it(self):
        stacking = Stacking()
        stacking.take(MAIN)
        stacking.take(hud_screen_name(LANDSCAPE))

        assert self._arranged(stacking, [_a_satellite(LANDSCAPE, hud=True)]) == [
            MAIN, LANDSCAPE, hud_screen_name(LANDSCAPE)]

    def test_the_main_hud_comes_forward_with_the_main_funestra_it_hangs_under(self):
        stacking = Stacking()
        stacking.take(LANDSCAPE)
        stacking.take(MAIN)

        assert self._arranged(stacking, [_a_satellite(LANDSCAPE)], RoomExtras(main_hud_unit=_a_main_hud())) == [
            LANDSCAPE, MAIN, MAIN_HUD]

    def test_the_dashboard_brings_no_main_hud_forward_while_a_video_wraps_the_viewer(self, tmp_path):
        stacking = Stacking()
        dash = _a_dash(tmp_path, wrapped=True, texture=SimpleNamespace(ready=True, aspect=2.5))
        stacking.take(LANDSCAPE)
        stacking.take(DASH)

        assert self._arranged(stacking, [_a_satellite(LANDSCAPE)],
                              RoomExtras(main_hud_unit=_a_main_hud(), dash=dash, wrapped=True)) == [MAIN, MAIN_HUD, LANDSCAPE, DASH]


class TestTheReferenceUnderTheDashboard:
    def _unit(self, tmp_path, *, carrying_the_main_hud=False):
        dash = SimpleNamespace(screen=SimpleNamespace(placement=SPOTS[DASH]),
                               texture=SimpleNamespace(aspect=_THE_DASHS_SHAPE),
                               carrying_the_main_hud=carrying_the_main_hud)
        main_hud_unit = SimpleNamespace(
            screen=SimpleNamespace(placement=Placement(0.0, 20.0, HUD_WIDTH_PX * DEG_PER_PX)),
            texture=SimpleNamespace(aspect=1.2))
        with patch("fun_time_vr.funestras.FrameTexture", _FakeMainHudTexture):
            unit = _ReferenceUnit(dash, main_hud_unit, flag=tmp_path / REFERENCE_OPEN_FILENAME)
        return unit, dash, main_hud_unit

    @staticmethod
    def _placed(unit) -> Placement:
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        return unit.screen.placement

    @staticmethod
    def _meets(upper: Placement, upper_aspect: float, lower: Placement) -> bool:
        above = surface_vertices(upper, aspect=upper_aspect)
        below = surface_vertices(lower, aspect=_FakeMainHudTexture.aspect)
        return below[:, 1].max() == pytest.approx(above[:, 1].min(), abs=1e-6)

    def test_it_hangs_from_the_dashboard_wherever_the_dashboard_goes(self, tmp_path):
        unit, dash, _main_hud = self._unit(tmp_path)

        under = self._placed(unit)
        dash.screen.placement = Placement(azimuth_deg=-40.0, elevation_deg=30.0, width_deg=40.0)
        followed = self._placed(unit)

        assert under.azimuth_deg == SPOTS[DASH].azimuth_deg
        assert self._meets(SPOTS[DASH], _THE_DASHS_SHAPE, under)
        assert followed.azimuth_deg == -40.0
        assert self._meets(dash.screen.placement, _THE_DASHS_SHAPE, followed)
        assert under.width_deg == followed.width_deg == REFERENCE_WIDTH_DEG

    def test_it_hangs_below_the_main_hud_while_the_main_hud_hangs_from_the_dashboard(
            self, tmp_path):
        unit, _dash, main_hud_unit = self._unit(tmp_path, carrying_the_main_hud=True)

        placed = self._placed(unit)

        assert placed.azimuth_deg == main_hud_unit.screen.placement.azimuth_deg
        assert self._meets(main_hud_unit.screen.placement, 1.2, placed)


class TestWhereTheDashboardHangs:
    def _placed(self, tmp_path, *, wrapped=False, remembered=None):
        dash = _a_dash(tmp_path, wrapped=wrapped, texture=_FakeMainHudTexture(),
                       remembered=remembered)
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()
        return dash

    def test_it_keeps_its_own_place_while_a_frame_is_in_the_slot(self, tmp_path):
        assert self._placed(tmp_path).screen.placement == SPOTS[DASH]

    def test_a_flat_video_does_not_move_it_with_the_main_hud(self, tmp_path):
        dash = self._placed(tmp_path)

        dash._main_unit.screen.placement = Placement(
            azimuth_deg=-40.0, elevation_deg=-25.0, width_deg=90.0)
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()

        assert dash.screen.placement == SPOTS[DASH]

    def test_a_flat_video_still_offers_it_to_the_pointer(self, tmp_path):
        screens = TestWhatThePointerCanReach()._screens(tmp_path)

        assert screens[DASH].pressable
        assert screens[DASH].movable

    def test_it_takes_the_pairs_place_while_it_carries_the_main_hud(self, tmp_path):
        assert self._placed(tmp_path, wrapped=True).screen.placement == SPOTS[MAIN_HUD]

    def test_a_drag_lands_in_the_spot_that_is_showing(self, tmp_path):
        put = Placement(azimuth_deg=25.0, elevation_deg=-12.0, width_deg=40.0)
        dash = self._placed(tmp_path, wrapped=True)

        assert dash.layout_key == MAIN_HUD
        dash.placement = put
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()

        assert dash.screen.placement == put

    def test_the_other_spot_is_untouched_by_it(self, tmp_path):
        dash = self._placed(tmp_path, wrapped=True)

        dash.placement = Placement(azimuth_deg=25.0, elevation_deg=-12.0, width_deg=40.0)

        assert dash._floating == SPOTS[DASH]
        assert dash.layout_key == MAIN_HUD

    def test_it_opens_in_whichever_of_the_two_the_last_session_left_it_in(self, tmp_path):
        floating = Placement(azimuth_deg=-30.0, elevation_deg=40.0, width_deg=44.0)
        carrying = Placement(azimuth_deg=15.0, elevation_deg=-20.0, width_deg=36.0)
        remembered = {DASH: floating, MAIN_HUD: carrying}
        own = DASH_WIDTH_PX * DASH_DEG_PER_PX

        assert self._placed(tmp_path, remembered=remembered).screen.placement == replace(
            floating, width_deg=own)
        assert self._placed(
            tmp_path, wrapped=True, remembered=remembered).screen.placement == replace(
            carrying, width_deg=own)

    def test_a_vr_reset_puts_both_of_its_spots_back_wherever_it_was_dragged(self, tmp_path):
        dash = self._placed(tmp_path)
        dash.placement = Placement(azimuth_deg=110.0, elevation_deg=5.0, width_deg=40.0)

        dash.put_back()
        with patch("fun_time_vr.funestras.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()

        assert dash.screen.placement == SPOTS[DASH]
        assert (dash._floating, dash._wrapped) == (SPOTS[DASH], SPOTS[MAIN_HUD])


_DRAGGED_OFF = Placement(azimuth_deg=-110.0, elevation_deg=-30.0, width_deg=15.0)


class TestTheSpotEachScreenStartsIn:
    def test_the_main_screen_is_one_of_the_movable_screens_hanging_dead_ahead(self):
        assert _MainUnit.SPOTS[MAIN] == Placement(0.0, 0.0, MAIN_WIDTH_DEG)

    def test_the_satellites_flank_the_main_screen_landscape_left_portrait_right(self):
        portrait, landscape = _SatelliteUnit.SPOTS[PORTRAIT], _SatelliteUnit.SPOTS[LANDSCAPE]

        assert landscape.azimuth_deg < 0 < portrait.azimuth_deg
        assert portrait.azimuth_deg == -landscape.azimuth_deg
        assert portrait.width_deg == landscape.width_deg
        assert portrait.elevation_deg == landscape.elevation_deg

    def test_the_satellites_tuck_inside_the_flush_position(self):
        landscape = _SatelliteUnit.SPOTS[LANDSCAPE]
        flush = (MAIN_WIDTH_DEG + landscape.width_deg) / 2

        assert abs(landscape.azimuth_deg) < flush

    def test_the_satellites_are_smaller_than_half_the_main_screen(self):
        assert _SatelliteUnit.SPOTS[LANDSCAPE].width_deg < MAIN_WIDTH_DEG / 2

    def test_the_satellites_ride_above_the_horizon(self):
        assert _SatelliteUnit.SPOTS[LANDSCAPE].elevation_deg > 0

    def test_the_gallery_hangs_clear_of_the_player_beside_it(self):
        gallery = _GalleryUnit.SPOTS[GALLERY]
        landscape = _SatelliteUnit.SPOTS[LANDSCAPE]

        assert (gallery.azimuth_deg + gallery.width_deg / 2
                < landscape.azimuth_deg - landscape.width_deg / 2)

    def test_the_gallery_opens_as_wide_as_the_browse(self):
        assert (_GalleryUnit.SPOTS[GALLERY].width_deg
                >= _LibraryUnit.SPOTS[LIBRARY].width_deg)

    def test_only_the_screens_a_controller_places_keep_one(self):
        assert set(SPOTS) == {MAIN, PORTRAIT, LANDSCAPE, MAIN_HUD, DASH, LIBRARY, GALLERY}

    def test_every_one_of_them_is_already_inside_the_scene(self):
        for name, spot in SPOTS.items():
            assert clamp_placement(spot) == spot, name


class TestTheHostedAppsScreenTakesTheSameHandlesAsAPlayer:
    def _hanging(self):
        unit = _like(_GalleryUnit, SimpleNamespace(
            showing=True,
            screen=SimpleNamespace(placement=_GalleryUnit.SPOTS[GALLERY]),
            texture=SimpleNamespace(ready=True, aspect=0.7)))
        (hanging,) = unit.hangings()
        return hanging.screen

    def test_it_is_dragged_and_pressed_like_the_players(self):
        screen = self._hanging()

        assert screen.movable
        assert screen.pressable

    def test_it_is_resized_by_the_squares_in_its_lower_corners(self):
        assert self._hanging().resizable

    def test_it_is_the_one_screen_that_takes_a_right_click(self):
        assert self._hanging().right_clicks


class TestPuttingTheRoomBack:
    @pytest.mark.parametrize("funestra", [PORTRAIT, LANDSCAPE])
    def test_a_satellite_goes_back_to_its_own_satellite(self, funestra):
        unit = _like(_SatelliteUnit, SimpleNamespace(
            screen_name=funestra, screen=SimpleNamespace(placement=_DRAGGED_OFF)))

        unit.put_back()

        assert unit.screen.placement == _SatelliteUnit.SPOTS[funestra]

    def test_the_main_funestra_goes_back_to_the_slot(self):
        unit = _like(_MainUnit, SimpleNamespace(
            screen_name=MAIN, screen=SimpleNamespace(placement=_DRAGGED_OFF)))

        unit.put_back()

        assert unit.screen.placement == _MainUnit.SPOTS[MAIN]

    def test_the_hosted_apps_window_goes_back_to_its_own_spot(self):
        unit = _like(_GalleryUnit, SimpleNamespace(
            screen=SimpleNamespace(placement=_DRAGGED_OFF)))

        unit.put_back()

        assert unit.screen.placement == _GalleryUnit.SPOTS[GALLERY]

    def test_the_browse_goes_back_to_its_own_spot(self, tmp_path):
        unit = _a_library(tmp_path, _FakeLibraryHost())
        unit.screen.placement = _DRAGGED_OFF

        unit.put_back()

        assert unit.screen.placement == _LibraryUnit.SPOTS[LIBRARY]

    @pytest.mark.parametrize("kind", [_MainHudUnit, _ReferenceUnit, LoadingScreenUnit])
    def test_what_rides_on_another_screen_keeps_no_spot_and_is_left_riding(self, kind):
        riding = SimpleNamespace(screen=SimpleNamespace(placement=_DRAGGED_OFF))

        kind.put_back(riding)

        assert kind.SPOTS == {}
        assert riding.screen.placement == _DRAGGED_OFF


class TestWhereItHangsToStart:
    def test_the_dash_opens_centered_over_the_main_funestra(self):
        assert SPOTS[DASH].azimuth_deg == SPOTS[MAIN].azimuth_deg

    def test_it_opens_two_reposition_handles_above_the_main_funestras_top(self):
        main_top = surface_vertices(SPOTS[MAIN], aspect=16 / 9)[:, 1].max()
        dash_lower_edge = surface_vertices(
            SPOTS[DASH], aspect=DASH_WIDTH_PX / dash_height())[:, 1].min()

        assert dash_lower_edge - main_top == pytest.approx(
            2 * RADIUS_M * np.radians(HANDLE_DEG), rel=0.05)

    def test_the_dash_is_as_wide_as_its_pixels_at_its_own_scale_in_either_of_its_spots(self):
        own = DASH_WIDTH_PX * DASH_DEG_PER_PX

        assert SPOTS[DASH].width_deg == pytest.approx(own)
        assert SPOTS[MAIN_HUD].width_deg == pytest.approx(own)
