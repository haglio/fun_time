"""fun_time_vr.player off the headset: the manifest contract and the room's wiring.

The scene, the eyes and the OpenXR frame loop need the real machine and stay
with the VR integration run; these pin what runs the same everywhere — how the
player is told about its session, what each Funestra is handed, and where each
screen hangs.
"""
from __future__ import annotations

import ast
import inspect
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import DEFAULT, patch

import numpy as np
import pytest
from app_support.file_channel import write_flag
from player_core.clip_picture import Picture
from player_core.console_hud import ModeHud
from player_core.hud_placement import HudEdge
from player_core.modes import MainMode
from player_core.play_points import play_points_filename
from player_core.playlist import PlaylistItem
from shared_ui.palette import BLUE

from fun_time.config import load_config
from fun_time.dashboard_actions import (
    BROWSE_LIBRARY_CLOSE,
    HELP_REFERENCE,
    LIBRARY_OPEN_FILENAME,
    QUIT_BUTTON,
    REFERENCE_OPEN_FILENAME,
)
from fun_time.event_log import NOTICE
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
from fun_time.shared_state import BridgeState, shared_state_path
from fun_time_vr import player, room
from fun_time_vr.console_panel import PANEL_WIDTH_DEG, PANEL_WIDTH_PX
from fun_time_vr.cover import (
    VR_SHUTDOWN_PHASES,
    VR_STARTUP_PHASES,
    WEARER_STATUS,
    CoverWatcher,
)
from fun_time_vr.dash_panel import DASH_WIDTH_PX, dash_actions, dash_height
from fun_time_vr.layout import (
    BANNER,
    DASH,
    LANDSCAPE,
    LIBRARY,
    MAIN,
    PANEL,
    PORTRAIT,
    REFERENCE,
    Layout,
    clamp_placement,
    read_layout,
    shown_at,
)
from fun_time_vr.library_panel import LIBRARY_SIZE_PX, scroll_from_stick, scroll_line
from fun_time_vr.notices import NoticeBoard
from fun_time_vr.orchestrator import build_vr_manifest
from fun_time_vr.player import (
    OFF_THE_PANEL,
    VrSettings,
    _BannerUnit,
    _ControllerPosts,
    _CoverUnit,
    _DashUnit,
    _draw_eyes,
    _hands_for_the_players,
    _HangingScreen,
    _LayoutKeeper,
    _LibraryUnit,
    _MainUnit,
    _PanelUnit,
    _PointerDrawing,
    _ReferenceUnit,
    _SatelliteUnit,
    _scene_is_up,
    _users,
    _VideoUnit,
    build_parser,
)
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
    RECTILINEAR_SBS,
)
from fun_time_vr.reference_panel import REFERENCE_WIDTH_DEG
from fun_time_vr.render import immersive_wrap
from fun_time_vr.satellite_hud import HUD_GAP_DEG, hud_screen_name
from fun_time_vr.scene import (
    MAIN_WIDTH_DEG,
    RADIUS,
    Placement,
    attached_below,
    surface_vertices,
    widened,
)
from fun_time_vr.stacking import Stacking
from fun_time_vr.surfaces import LatestPicture, PanelBitmap
from fun_time_vr.video_thread import VideoThread
from fun_time_vr.wrap_readout import WrapReadout
from main_player.genau import GenauChannels
from tests.satellite_fakes import FakeSatellitePlayer


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

# The collaborators a unit builds that need libmpv, a GL context, a socket, the
# library or the state directory.  Faked wholesale: what is under test here is
# which manifest field each path comes from, not what is done with it afterwards.
_UNIT_COLLABORATORS = (
    "HeadsetPlayer", "VideoThread", "RenderTarget", "FrameTexture", "Funestra",
    "HeadsetVerbs", "PictureLook", "_TheLibrary", "FunscriptTCodeDriver", "UdpTCodeSink",
)

_A_SESSION = VrSettings(tcode_udp_host="127.0.0.1", tcode_udp_port=8000, library_dirs=(),
                        audio_device="", compositor_layers=False)


# The video thread is faked above, so no GL context is ever asked for.
_NO_GL_CONTEXTS = None

# Where each screen starts, read off the units, there being no one table of them.
SPOTS = {name: spot for kind in vars(player).values() if isinstance(kind, type)
         for name, spot in getattr(kind, "SPOTS", {}).items()}


def _manifest_for_a_vr_session(tmp_path) -> LaunchManifest:
    """A real manifest, written by the writer the VR launcher uses, whose
    channel files live in the test's own folder: the example config names a
    `state` beside the checkout, which a session run from this checkout
    writes into, and a command it left there reached a unit under test."""
    config = load_config(Path("fun_time_config.example.json"))
    config = replace(config, paths=replace(config.paths, state_dir=tmp_path,
                                           broker_state_dir=tmp_path))
    path = write_manifest_data(
        build_vr_manifest(config), tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
    return LaunchManifest.read(path)


def test_a_test_sessions_channel_files_live_in_its_own_folder(tmp_path):
    manifest = _manifest_for_a_vr_session(tmp_path)

    assert Path(manifest.commands.main_player_cmd_file).is_relative_to(tmp_path)
    assert Path(manifest.commands.genau_cmd_file).is_relative_to(tmp_path)


@pytest.fixture
def faked_collaborators():
    """Every unit collaborator recorded rather than built."""
    with patch.multiple(
        "fun_time_vr.player", **dict.fromkeys(_UNIT_COLLABORATORS, DEFAULT)
    ) as fakes, \
            patch("fun_time_vr.player.read_playlist", return_value=[]):
        yield fakes


def _a_main_unit(tmp_path, *, remembered=None, vr=_A_SESSION, manifest=None) -> _MainUnit:
    return _MainUnit(manifest or _manifest_for_a_vr_session(tmp_path), vr, _NO_GL_CONTEXTS,
                     remembered=remembered or Layout(), stop=threading.Event())


def test_the_main_slot_opens_where_the_last_session_left_it(tmp_path, faked_collaborators):
    """Genau's picture and the video take turns in the one slot, so both open in
    whatever the last drag on it settled."""
    moved = Placement(azimuth_deg=18.0, elevation_deg=-6.0, width_deg=95.0)

    unit = _a_main_unit(tmp_path, remembered=Layout({MAIN: moved}))

    assert unit.screen.placement == moved


def test_the_main_slot_opens_at_the_tilt_the_last_session_left_it_at(
        tmp_path, faked_collaborators):
    """The tilt is the whole arrangement's, and this unit is the one that carries
    it, so a session that opened level would stand every screen up again."""
    _a_main_unit(tmp_path, remembered=Layout(tilt_deg=-17.5))

    assert faked_collaborators["HeadsetVerbs"].call_args.kwargs["tilt_deg"] == -17.5


def test_the_main_funestra_is_handed_every_file_it_needs_out_of_the_manifest(
        tmp_path, faked_collaborators):
    """A spelling that no longer resolves raises here rather than on the
    headset, where the unit is built with no console to say so."""
    manifest = _manifest_for_a_vr_session(tmp_path)
    vr = replace(_A_SESSION, audio_device="Example Headset")

    unit = _a_main_unit(tmp_path, vr=vr, manifest=manifest)

    commands = manifest.commands
    channels = faked_collaborators["Funestra"].call_args.kwargs["channels"]
    assert channels.playlist == Path(commands.main_player_playlist_file)
    assert channels.command == Path(commands.main_player_cmd_file)
    assert channels.paused == Path(commands.main_player_paused_file)
    assert channels.status == Path(commands.main_player_status_file)
    assert channels.play_points == Path(commands.state_dir) / play_points_filename("main_player")
    assert channels.console == Path(commands.main_player_console_file)
    assert channels.dashboard_cmd == Path(commands.dashboard_cmd_file)
    assert channels.drive == Path(commands.genau_drive_file)
    # The one that is not a path: without it `route_audio` never asks mpv for the
    # headset's sink, and the main player's sound stays on the room speakers.
    assert unit._audio_device == "Example Headset"


def test_the_main_funestra_is_the_desktops_with_the_headsets_own_surfaces(
        tmp_path, faked_collaborators):
    """Locked and sounding as the room says, as the desktop's opens; its panel
    on a bitmap held to the console's one width, a User's picture handed over,
    and the headset's own verbs asked after what runs on it."""
    unit = _a_main_unit(tmp_path)

    handed = faked_collaborators["Funestra"].call_args.kwargs
    assert (handed["locked"], handed["sound_is_the_rooms"]) == (True, True)
    assert handed["panel_surface"] is unit.panel
    assert unit.panel.width == PANEL_WIDTH_PX
    assert handed["users_picture"] is unit.users_picture
    handed["window_verbs"]("TILT_UP")
    unit.verbs.apply.assert_called_once_with("TILT_UP")


def test_kino_runs_on_it_through_the_library_and_the_memory_the_desktop_reads(
        tmp_path, faked_collaborators):
    manifest = _manifest_for_a_vr_session(tmp_path)

    _a_main_unit(tmp_path, manifest=manifest)

    library = faked_collaborators["_TheLibrary"]
    assert library.call_args.args == (manifest, _A_SESSION)
    users = faked_collaborators["Funestra"].call_args.kwargs["users"]
    assert users == {MainMode.KINO: library.return_value.kino.return_value}
    assert library.return_value.kino.call_args.args == (
        Path(manifest.commands.main_player_notice_file),)
    assert (faked_collaborators["Funestra"].call_args.kwargs["playlist"]
            is library.return_value.playlist.return_value)


def test_the_main_slot_drives_the_headsets_osr2_inlet(tmp_path, faked_collaborators):
    vr = replace(_A_SESSION, tcode_udp_host="127.0.0.9", tcode_udp_port=8123)

    _a_main_unit(tmp_path, vr=vr)

    assert faked_collaborators["UdpTCodeSink"].call_args.args == ("127.0.0.9", 8123)
    assert (faked_collaborators["Funestra"].call_args.kwargs["tcode"]
            is faked_collaborators["FunscriptTCodeDriver"].return_value)


class TestGenauOnTheMainFunestra:
    """Genau runs on the headset's Main Funestra as it runs on the desktop's,
    where the session names its clips folder."""

    def _users(self, tmp_path, *, clips_folder, resumed=None):
        manifest = _manifest_for_a_vr_session(tmp_path)
        vr = replace(_A_SESSION, clips_folder=clips_folder)
        stop = threading.Event()
        with patch.multiple("fun_time_vr.player", GenauInTheHeadset=DEFAULT,
                            GenauNotifier=DEFAULT, UdpTCodeSink=DEFAULT) as fakes, \
                patch("fun_time_vr.player.read_genau_status",
                      return_value=SimpleNamespace(clip="")), \
                patch("fun_time_vr.player.read_shared_state", return_value=resumed) as read_state:
            users = _users(manifest, vr, stop, kino="kino")
            if MainMode.GENAU in users:
                users[MainMode.GENAU](None)
        return SimpleNamespace(users=users, manifest=manifest, stop=stop, fakes=fakes,
                               read_state=read_state)

    def test_a_session_naming_no_clips_folder_runs_kino_alone(self, tmp_path):
        assert self._users(tmp_path, clips_folder=None).users == {MainMode.KINO: "kino"}

    def test_a_session_naming_one_puts_genau_beside_kino(self, tmp_path):
        built = self._users(tmp_path, clips_folder=tmp_path)

        assert set(built.users) == {MainMode.KINO, MainMode.GENAU}
        handed = built.fakes["GenauInTheHeadset"].call_args.kwargs
        commands = built.manifest.commands
        assert handed["clips_folder"] == tmp_path
        assert handed["channels"] == GenauChannels(
            command=Path(commands.genau_cmd_file), paused=Path(commands.genau_paused_file),
            status=Path(commands.genau_status_file), drive=Path(commands.genau_drive_file))
        assert handed["stop_event"] is built.stop
        assert handed["metadata_root"] == Path(built.manifest.regen.metadata_root)

    def test_it_opens_in_the_order_the_last_session_left_it_browsing(self, tmp_path):
        """Latest is handed to Genau beside the clip, off the state the
        orchestrator just resumed into the session's state dir: as a verb once
        Genau is up, LATEST would browse the new order from its top, over that clip."""
        built = self._users(tmp_path, clips_folder=tmp_path, resumed=BridgeState(genau_latest=True))

        assert built.read_state.call_args.args == (
            shared_state_path(Path(built.manifest.commands.state_dir)),)
        assert built.fakes["GenauInTheHeadset"].call_args.kwargs["latest"] is True


def test_a_side_screen_hands_its_funestra_the_scripts_its_playlist_names(
        tmp_path, faked_collaborators):
    clip, script, unscripted = tmp_path / "v0.mp4", tmp_path / "v0.funscript", tmp_path / "v1.mp4"
    items = [PlaylistItem(clip, script), PlaylistItem(unscripted)]

    with patch("fun_time_vr.player.read_playlist", return_value=items):
        _SatelliteUnit(PORTRAIT, _manifest_for_a_vr_session(tmp_path), _NO_GL_CONTEXTS,
                       vr=_A_SESSION, remembered={})

    assert faked_collaborators["Funestra"].call_args.kwargs["playlist"] == items


def test_a_side_screen_drives_the_headsets_osr2_inlet_with_its_clips_script(
        tmp_path, faked_collaborators):
    vr = replace(_A_SESSION, tcode_udp_host="127.0.0.9", tcode_udp_port=8123)

    _SatelliteUnit(PORTRAIT, _manifest_for_a_vr_session(tmp_path), _NO_GL_CONTEXTS,
                   vr=vr, remembered={})

    assert faked_collaborators["UdpTCodeSink"].call_args.args == ("127.0.0.9", 8123)
    assert (faked_collaborators["Funestra"].call_args.kwargs["tcode"]
            is faked_collaborators["FunscriptTCodeDriver"].return_value)


@pytest.mark.parametrize("player", ["portrait", "landscape"])
def test_a_satellite_unit_finds_every_file_it_needs_in_the_manifest(
        player, tmp_path, faked_collaborators):
    """Six paths per player, five of them asked for by player rather than spelled
    out — and the sixth, the dashboard's command file, shared with the desktop."""
    manifest = _manifest_for_a_vr_session(tmp_path)

    unit = _SatelliteUnit(player, manifest, _NO_GL_CONTEXTS, vr=_A_SESSION, remembered={})

    commands = manifest.commands
    handed = faked_collaborators["Funestra"].call_args.kwargs
    channels = handed["channels"]
    assert channels.command == Path(commands.player_file(player, "cmd"))
    assert channels.paused == Path(commands.player_file(player, "paused"))
    assert channels.playlist == Path(commands.player_file(player, "playlist"))
    assert channels.status == Path(commands.player_file(player, "status"))
    assert channels.play_points == Path(commands.state_dir) / play_points_filename(player)
    assert channels.hud == Path(commands.player_file(player, "hud"))
    assert channels.dashboard_cmd == Path(commands.dashboard_cmd_file)
    # The HUD paints onto a bitmap of its own, hanging under the picture, not
    # into the video through mpv as the desktop satellite's does.
    assert handed["panel_surface"] is unit.panel


class _AVideoThread:
    """The video thread with a fake engine on it and no GL: what a unit reads
    off it, and the player it hands the Funestra."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.player = FakeSatellitePlayer(duration_ms=600_000.0)
        self.closed = False

    @property
    def duration_ms(self) -> float:
        return self.player.duration_ms

    def show_newest(self, _target) -> bool:
        return False

    def ask_for_a_still(self) -> None:
        pass

    def take_a_still(self):
        return None

    def close(self) -> None:
        self.closed = True


class _AKino:
    """What runs on the Funestra, as far as these tests look."""

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

    def picture(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _ALibrary:
    def __init__(self, tmp_path, *_args, **_kwargs) -> None:
        self._video = tmp_path / "feature.mp4"
        self._video.write_bytes(b"")

    def playlist(self) -> list[PlaylistItem]:
        return [PlaylistItem(self._video)]

    def kino(self, _notice_file):
        return _AKino


class _FakeRenderTarget:
    """A sized-nothing target: no GL, and no picture yet."""

    def __init__(self) -> None:
        self.width = self.height = 0
        self.painted = False
        self.video = None
        self.texture = 0

    ready = False
    has_picture = False
    aspect = 16 / 9

    def close(self) -> None:
        pass


@pytest.fixture
def a_fake_engine(tmp_path):
    """The units over a real Funestra: the engine, the GL and the library
    faked, everything the Funestra does real."""
    with patch.multiple("fun_time_vr.player", VideoThread=_AVideoThread,
                        RenderTarget=_FakeRenderTarget, FrameTexture=_FakeTexture,
                        _TheLibrary=lambda *args, **kwargs: _ALibrary(tmp_path),
                        UdpTCodeSink=DEFAULT):
        yield


def _dashboard_asked(tmp_path) -> list[str]:
    path = tmp_path / "dashboard_cmd.txt"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


class TestTheMainFunestrasPass:
    def test_the_main_screen_blends_nothing_into_its_picture(self, tmp_path, a_fake_engine):
        """The console goes onto the bitmap hanging under the slot, wrapped or
        flat, so the picture itself comes through untouched."""
        unit = _a_main_unit(tmp_path)

        unit.pump(threading.Event(), 0.0)

        assert unit.player.overlays == {}
        assert unit.panel.take()[0] is not None

    def test_a_squeeze_on_the_picture_asks_the_room_to_pause(self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)

        unit.point(Frame(events=(PressEvent(PRESS, MAIN, 0.5, 0.5), PressEvent(RELEASE, MAIN))))
        unit.pump(threading.Event(), 0.0)

        assert _dashboard_asked(tmp_path) == ["omnipause_toggle"]

    def test_a_squeeze_on_the_panel_does_not(self, tmp_path, a_fake_engine):
        """The panel places a press on its own buttons; what lands on none of
        them is the panel's all the same, never the picture's."""
        unit = _a_main_unit(tmp_path)
        unit.pump(threading.Event(), 0.0)

        unit.point(Frame(events=(PressEvent(PRESS, PANEL, 0.999, 0.001), PressEvent(RELEASE, PANEL))))
        unit.pump(threading.Event(), 0.0)

        assert _dashboard_asked(tmp_path) == []

    def test_quit_stops_the_room(self, tmp_path, a_fake_engine):
        manifest = _manifest_for_a_vr_session(tmp_path)
        unit = _a_main_unit(tmp_path, manifest=manifest)
        Path(manifest.commands.main_player_cmd_file).write_text("QUIT\n", encoding="utf-8")
        stop = threading.Event()

        unit.pump(stop, 0.0)

        assert stop.is_set()

    def test_closing_closes_what_runs_on_it_after_the_engine(self, tmp_path, a_fake_engine):
        unit = _a_main_unit(tmp_path)

        unit.close()

        assert unit.video.closed
        assert unit.player.closed


class TestASqueezeOnAHangingPanel:
    """A press on the hanging screen lands in the panel's own pixels; the unit
    puts it back where the Funestra drew the panel and the Funestra places it."""

    def _unit(self):
        presses = []
        unit = _VideoUnit.__new__(_VideoUnit)
        unit._held = False
        unit.panel = PanelBitmap()
        unit.panel.overlay(10, 8, 300, np.zeros((100, 200, 4), dtype=np.uint8))
        unit.target = SimpleNamespace(width=640, height=480)
        unit.funestra = SimpleNamespace(
            press=lambda x, y, window: presses.append(("press", x, y, window)),
            motion=lambda x, y, held, window: presses.append(("motion", x, y, held)),
            release=lambda: presses.append(("release",)))
        return unit, presses

    def test_a_press_is_placed_at_the_panels_own_pixel_where_it_was_drawn(self):
        unit, presses = self._unit()

        unit._press_on_the_panel(PressEvent(PRESS, PANEL, 0.25, 0.5))

        assert presses == [("press", 8 + 50, 300 + 50, (640, 480))]

    def test_a_drag_keeps_the_press_held_and_a_release_lets_go(self):
        unit, presses = self._unit()

        unit._press_on_the_panel(PressEvent(PRESS, PANEL, 0.25, 0.5))
        unit._press_on_the_panel(PressEvent(DRAG, PANEL, 0.5, 0.5))
        unit._press_on_the_panel(PressEvent(RELEASE, PANEL))

        assert presses[1:] == [("motion", 8 + 100, 300 + 50, True), ("release",)]

    def test_hovering_the_panel_names_the_button_under_the_pointer(self):
        unit, presses = self._unit()

        unit._hover_over_the_panel((PANEL, (0.25, 0.5)), PANEL)

        assert presses == [("motion", 8 + 50, 300 + 50, False)]

    def test_a_pointer_off_the_panel_leaves_no_tooltip(self):
        unit, presses = self._unit()

        unit._hover_over_the_panel((MAIN, (0.25, 0.5)), PANEL)
        unit._hover_over_the_panel(None, PANEL)

        assert presses == [("motion", *OFF_THE_PANEL, False)] * 2

    def test_a_held_press_is_not_moved_by_the_hover(self):
        unit, presses = self._unit()
        unit._press_on_the_panel(PressEvent(PRESS, PANEL, 0.25, 0.5))

        unit._hover_over_the_panel((PANEL, (0.5, 0.5)), PANEL)

        assert len(presses) == 1

    def test_a_press_before_the_panel_is_drawn_lands_nowhere(self):
        unit, presses = self._unit()
        unit.panel = PanelBitmap()

        unit._press_on_the_panel(PressEvent(PRESS, PANEL, 0.25, 0.5))

        assert presses == []


# Every class that goes on the file-channel worker's list, which the frame
# loop's `finally` then closes one by one: `pumped` in `player._run`.
_EVERYTHING_THE_WORKER_IS_HANDED = [
    NoticeBoard, _MainUnit, _SatelliteUnit, _DashUnit, _PanelUnit, _ReferenceUnit,
    _LibraryUnit, _BannerUnit, _CoverUnit, _LayoutKeeper, _ControllerPosts,
]


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
        assert unit_class.pump is not _VideoUnit.pump
        assert unit_class.close is not _VideoUnit.close
        assert list(inspect.signature(unit_class.pump).parameters) == [
            "self", "stop", "now"]

    @pytest.mark.parametrize("pumped_class", _EVERYTHING_THE_WORKER_IS_HANDED)
    def test_everything_the_worker_is_handed_answers_both_calls(self, pumped_class):
        """The list is not all video units -- the notice board and the layout
        keeper ride on it too -- so nothing about being a `_VideoUnit` covers
        it.  The board answered pump and not close, and since it is FIRST on
        the list the teardown died on its very first entry: every session, for
        as long as the board has been on it, and every crossing paid the
        orchestrator's whole wait for a hold that could no longer be reported.
        """
        assert callable(getattr(pumped_class, "pump", None))
        assert callable(getattr(pumped_class, "close", None))


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


def _a_satellite_unit(tmp_path, player, remembered):
    vr = VrSettings(
        tcode_udp_host="127.0.0.1", tcode_udp_port=8000, library_dirs=(),
        audio_device="", compositor_layers=False,
    )
    return _SatelliteUnit(player, _manifest_for_a_vr_session(tmp_path), _NO_GL_CONTEXTS,
                          vr=vr, remembered=remembered)


def test_a_satellite_hangs_where_the_layout_says(tmp_path, faked_collaborators):
    moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

    unit = _a_satellite_unit(tmp_path, PORTRAIT, {PORTRAIT: moved})

    assert unit.screen.placement == moved


def test_a_satellite_the_layout_says_nothing_about_hangs_in_its_own_spot(
        tmp_path, faked_collaborators):
    """A remembered layout names only the screens a session actually moved, so
    the one that was left alone has to find its opening spot for itself."""
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
        assert not path.exists()  # a screen mid-drag is not worth a file yet

        keeper.settle()
        keeper.pump(threading.Event(), 0.0)
        assert read_layout(path).placements[PORTRAIT] == moved

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

        assert read_layout(path) == Layout({PORTRAIT: moved}, 12.5)

    def test_being_told_the_tilt_it_already_holds_is_not_worth_a_file(self, tmp_path):
        """Every frame says what the tilt is, so only a change may owe a write."""
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout(tilt_deg=12.5))

        keeper.tilt(12.5, carried=False)
        keeper.close()

        assert not path.exists()

    def test_a_tilt_off_a_keypress_is_finished_the_moment_it_lands(self, tmp_path):
        """PgUp is a whole act, not the middle of one, so it does not wait for a
        release that never comes."""
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())

        keeper.tilt(5.0, carried=False)
        keeper.pump(threading.Event(), 0.0)

        assert read_layout(path).tilt_deg == 5.0

    def test_a_tilt_a_hand_is_still_lifting_waits_for_the_release(self, tmp_path):
        """The same reason a screen mid-drag is not worth a file: the gesture
        writes once, where it was let go, not once a frame."""
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())

        keeper.tilt(5.0, carried=True)
        keeper.pump(threading.Event(), 0.0)
        assert not path.exists()

        keeper.settle()
        keeper.pump(threading.Event(), 0.0)

        assert read_layout(path).tilt_deg == 5.0

    def test_a_session_ending_mid_drag_still_keeps_the_screen_where_it_was_left(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())
        moved = Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0)

        keeper.place(PORTRAIT, moved)
        keeper.close()

        assert read_layout(path).placements[PORTRAIT] == moved

    def test_putting_the_room_back_leaves_the_session_remembering_nothing(self, tmp_path):
        """The file is only where he moved a screen TO and the tilt he left it at,
        so a room put back has nothing to say -- and each screen then opens in its
        own unit's spot, level."""
        path = tmp_path / "vr_layout.json"
        keeper = _LayoutKeeper(path, Layout())
        keeper.place(PORTRAIT, Placement(azimuth_deg=-60.0, elevation_deg=-5.0, width_deg=20.0))
        keeper.tilt(-20.0, carried=False)
        keeper.settle()
        keeper.pump(threading.Event(), 0.0)
        assert read_layout(path) != Layout()

        keeper.forget()
        keeper.settle()
        keeper.pump(threading.Event(), 0.0)

        assert read_layout(path) == Layout()


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


class _FakePanelTexture:
    """A FrameTexture with real numbers where the panel does arithmetic on them."""

    ready = True
    aspect = 280 / 120
    width = PANEL_WIDTH_PX

    def upload(self, pixels):
        self.uploaded = pixels

    def close(self):
        pass


class TestThePanelUnderTheSlot:
    """The console in the headset: the bitmap the Main Funestra draws, docked
    under whichever picture fills the main slot -- and, while the video wraps
    the viewer and there is nothing to dock to, under the dashboard."""

    def _unit(self, *, wrapped=False, showing=False, edge=HudEdge.LOWER):
        projection = EQUIRECT_180_SBS if wrapped else FLAT
        main_unit = _like(_MainUnit, _AMainStandIn(
            owns_the_slot=not showing,
            funestra=SimpleNamespace(panel_edge=edge),
            panel=PanelBitmap(width=PANEL_WIDTH_PX),
            verbs=SimpleNamespace(projection_of=lambda _video: projection, **_NEVER_DIALED),
            target=SimpleNamespace(ready=True, aspect=16 / 9, video=None),
            users_picture_texture=SimpleNamespace(ready=True, aspect=4 / 3),
            _clip_projection=(Path("clip.mp4"), projection),
            screen=SimpleNamespace(placement=SPOTS[MAIN]),
        ))
        dash = SimpleNamespace(texture=SimpleNamespace(ready=True, aspect=560 / 218),
                               screen=SimpleNamespace(placement=SPOTS[PANEL]))
        with patch("fun_time_vr.player.FrameTexture", _FakePanelTexture):
            unit = _PanelUnit(main_unit, dash)
        main_unit.panel.overlay(10, 8, 8, np.zeros((120, PANEL_WIDTH_PX, 4), dtype=np.uint8))
        return SimpleNamespace(unit=unit, main_unit=main_unit, dash=dash)

    @staticmethod
    def _placed(unit) -> Placement:
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        return unit.screen.placement

    def test_the_bitmap_the_main_funestra_drew_is_what_it_hangs(self):
        p = self._unit()

        self._placed(p.unit)

        assert p.unit.texture.uploaded.shape == (120, PANEL_WIDTH_PX, 4)

    def test_the_console_rides_under_the_main_player_wherever_it_goes(self):
        """Docked the way a satellite's HUD is docked, rather than placed: and
        re-placed every frame, not only the ones that repaint it, so it cannot lag
        the player it belongs to -- which is what left its old handle dragging
        an empty rectangle around for seconds at a time."""
        p = self._unit()

        docked = self._placed(p.unit)
        p.main_unit.screen.placement = Placement(
            azimuth_deg=-40.0, elevation_deg=12.0, width_deg=110.0)
        followed = self._placed(p.unit)

        assert docked.azimuth_deg == SPOTS[MAIN].azimuth_deg
        assert docked.elevation_deg < 0.0  # under the picture, never over it
        assert followed.azimuth_deg == -40.0
        assert followed.elevation_deg < docked.elevation_deg  # a bigger player hangs lower
        assert followed.width_deg == docked.width_deg == PANEL_WIDTH_DEG

    def test_it_rides_under_genaus_picture_at_the_clips_own_shape(self):
        p = self._unit(showing=True)

        placed = self._placed(p.unit)

        clip = p.main_unit.users_picture_texture.aspect
        assert placed == attached_below(
            shown_at(MAIN, p.main_unit.screen.placement, clip), aspect=clip,
            width_deg=PANEL_WIDTH_DEG, hanging_aspect=_FakePanelTexture.aspect, gap_deg=HUD_GAP_DEG)

    def test_it_hangs_along_the_edge_the_room_moved_it_to(self):
        p = self._unit(edge=HudEdge.UPPER)

        placed = self._placed(p.unit)

        assert placed.elevation_deg > 0.0  # over the picture

    def test_a_wrapped_console_hangs_from_the_dashboard_instead(self):
        """No picture to dock to, so it docks to the one thing above it -- which
        is what carries the handle the pair is moved by."""
        p = self._unit(wrapped=True, edge=HudEdge.UPPER)

        placed = self._placed(p.unit)

        assert placed == attached_below(
            p.dash.screen.placement, aspect=p.dash.texture.aspect,
            width_deg=PANEL_WIDTH_DEG, hanging_aspect=_FakePanelTexture.aspect)

    def test_it_meets_the_dashboard_with_nothing_between_them(self):
        """The strip is left off there -- empty, it read as a gap the width of a
        handle between the two panels, which is what a handle looks like."""
        p = self._unit(wrapped=True)

        placed = self._placed(p.unit)

        console = surface_vertices(placed, aspect=_FakePanelTexture.aspect)
        over = surface_vertices(p.dash.screen.placement, aspect=p.dash.texture.aspect)
        assert console[:, 1].max() == pytest.approx(over[:, 1].min(), abs=1e-6)

    def test_it_is_pressed_and_never_dragged(self):
        p = self._unit()
        self._placed(p.unit)

        (hanging,) = p.unit.hangings()

        assert hanging.screen.name == PANEL
        assert hanging.screen.pressable and not hanging.screen.movable
        assert hanging.docked_to == MAIN

    def test_before_the_main_funestra_has_drawn_it_there_is_nothing_to_hang(self):
        p = self._unit()
        p.main_unit.panel = PanelBitmap()
        p.unit.texture = _FakeTexture()

        self._placed(p.unit)

        assert p.unit.hangings() == ()


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

    def test_it_says_it_is_waiting_on_him_once_the_room_is_up(
            self, tmp_path, cover_graphics):
        """A room that is finished is not waiting for players, and saying so
        while he has the headset off is the only moment the words can reach
        him -- they have to be right before he looks, not after."""
        PhaseProgress(tmp_path / PROGRESS_FILENAME,
                      phases=VR_STARTUP_PHASES).advance("players")
        unit = _CoverUnit(tmp_path)
        assert unit._cover.status == "Waiting for players..."

        unit.awaiting_wearer = True
        unit.pump(threading.Event(), 0.0)

        assert unit._cover.status == WEARER_STATUS
        assert unit._cover.fraction == pytest.approx(
            CoverWatcher(tmp_path).read().fraction
        ), "only the words change; the bar still says how far the launch got"

    def test_a_teardown_never_asks_him_to_put_it_on(self, tmp_path, cover_graphics):
        """The closing cover is shown to someone already wearing it."""
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("players")
        unit = _CoverUnit(tmp_path)

        unit.awaiting_wearer = True
        unit.pump(threading.Event(), 0.0)

        assert unit._cover.status != WEARER_STATUS

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


def _picture(painted):
    """A texture that is always sized, and holds a picture only when *painted*
    -- the two states the room gate has to tell apart."""
    return SimpleNamespace(ready=True, has_picture=painted)


def _room(*, main=True, portrait=True, landscape=True, panel=True):
    return dict(
        main_unit=SimpleNamespace(picture_in_the_slot=_picture(main)),
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

    def test_a_sized_texture_is_not_a_picture(self):
        """A video target is ready the moment mpv reports the clip's
        dimensions, a frame or more before it presents anything -- so the room
        read as up almost as soon as the loop began, the cover came off in a
        blink nobody saw, and the OSR2 was released onto black."""
        scene = _room()
        scene["main_unit"] = SimpleNamespace(picture_in_the_slot=SimpleNamespace(
            ready=True, has_picture=False,
        ))

        assert not _scene_is_up(**scene)

    def test_a_target_is_marked_painted_where_its_picture_lands(self):
        """The other half of that contract, pinned in the source because the copy
        itself needs a GL context: the flag the gate reads is set where a picture
        is copied in, never beside the ``ensure`` that only sizes the texture."""
        source = ast.unparse(ast.parse(inspect.getsource(VideoThread.show_newest).lstrip()))
        before, copied, after = source.partition("glCopyImageSubData")
        assert copied
        assert "target.painted = True" in after
        assert "target.painted = True" not in before

    def test_the_main_slot_counts_once_wherever_the_scene_is(self):
        """In genau mode Genau's picture has the scene and the video waits
        paused under it, so asking the video for a picture would hold the
        cover over a room that is finished."""
        unit = _like(_MainUnit, SimpleNamespace(
            owns_the_slot=False, target=_picture(False),
            users_picture_texture=_picture(True)))

        assert _MainUnit.picture_in_the_slot.fget(unit) is unit.users_picture_texture


def test_the_cover_goes_up_before_the_players_are_built():
    """Built first and shown after, it was on screen for the tail of a launch
    that had already finished -- which is why none of it was ever seen."""
    tree = ast.parse(inspect.getsource(player._run))
    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.setdefault(ast.unparse(node.func), node.lineno)

    assert calls["_CoverUnit"] < calls["_MainUnit"]
    assert calls["_raise_the_cover"] < calls["_MainUnit"]
    assert calls["_raise_the_cover"] < calls["_SatelliteUnit"]
    assert calls["_raise_the_cover"] < calls["_PanelUnit"]


def _keyword_given(source_of, call: str, keyword: str) -> str:
    tree = ast.parse(inspect.getsource(source_of).lstrip())
    (built,) = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call) and ast.unparse(node.func) == call]
    return next(ast.unparse(given.value) for given in built.keywords if given.arg == keyword)


def test_the_headsets_genau_keeps_its_flips_where_the_library_keeps_its_records():
    assert _keyword_given(player._users, "GenauInTheHeadset", "metadata_root") == (
        "_metadata_root(manifest)")


def test_the_headsets_browse_asks_whether_genau_has_the_main_slot():
    assert _keyword_given(player._run, "_LibraryUnit", "genau_has_the_slot") == (
        "lambda: not main_unit.owns_the_slot")


def test_the_main_funestra_keeps_the_cover_moving_while_it_reads_the_library():
    """Reading the library takes seconds on a cold cache, and nothing else
    presents a frame while the units are built."""
    assert _keyword_given(player._run, "_MainUnit", "while_reading") == (
        "lambda: _present_the_cover(session, renderer, cover)")


def test_the_main_funestra_ticks_on_a_worker_of_its_own_at_genaus_rate():
    tree = ast.parse(inspect.getsource(player._run))
    workers = {
        ast.unparse(call.keywords[-1].value): call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and ast.unparse(call.func) == "start_daemon_thread"
    }

    main = workers["'main-funestra'"]
    assert ast.unparse(next(k.value for k in main.keywords if k.arg == "args")) == (
        "([main_unit], stop, perf)")
    assert ast.unparse(next(k.value for k in main.keywords if k.arg == "kwargs")) == (
        "{'hz': MAIN_TICK_HZ}")
    assert ast.unparse(next(k.value for k in workers["'file-channels'"].keywords
                            if k.arg == "args")) == "(pumped, stop, perf)"


@pytest.mark.parametrize("screen", ["dash", "library"])
def test_every_screen_is_registered_in_the_room(screen):
    """Out of the room a screen is never painted, pumped, pointed at, drawn or
    closed.  The dash was pumped, pointed at and placed in the layout and left
    out of the eye pass, so it existed everywhere except in front of him."""
    tree = ast.parse(inspect.getsource(player._run))
    (units,) = [node for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "units"]

    assert screen in ast.unparse(units.value)


def test_everything_the_room_closes_when_it_ends_has_a_close():
    tree = ast.parse(inspect.getsource(player._run))
    built = {node.targets[0].id: node.value for node in ast.walk(tree)
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    (closing,) = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == "_close_channels"]

    def made_by(expr):
        if isinstance(expr, ast.Name):
            yield from made_by(built[expr.id])
        elif isinstance(expr, ast.Starred):
            yield from made_by(expr.value)
        elif isinstance(expr, (ast.List, ast.Tuple)):
            for element in expr.elts:
                yield from made_by(element)
        elif isinstance(expr, ast.ListComp):
            yield from made_by(expr.elt)
        elif isinstance(expr, ast.Call):
            yield getattr(player, ast.unparse(expr.func))

    closed = list(made_by(closing.args[0]))

    assert player.NoticeBoard in closed
    assert [kind.__name__ for kind in closed if not callable(getattr(kind, "close", None))] == []


def test_the_reveal_waits_for_the_cover_to_have_been_seen():
    """The room being drawable is not the same as anyone having had the headset
    on while it was covered."""
    tree = ast.parse(inspect.getsource(player._run))
    (note,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and ast.unparse(n.func) == "scene_ready.note"]
    (room,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "room_is_up"]

    assert "cover_seen.dwelt" in ast.unparse(note)
    assert "room_is_up" in ast.unparse(note)
    assert "_scene_is_up" in ast.unparse(room.value)


def test_the_cover_is_told_it_is_waiting_on_him_only_once_the_room_is_up():
    """Said before that, it would blame him for a launch still building the
    room; said off the same value the reveal reads, the two cannot disagree."""
    tree = ast.parse(inspect.getsource(player._run))
    (told,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and ast.unparse(n.targets[0]) == "cover.awaiting_wearer"]

    (settled,) = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Assign)
                  and ast.unparse(n.targets[0]) == "cover.anchor_settled"]
    assert ast.unparse(settled.value) == "cover_seen.dwelt and session.views_tracked"

    assert ast.unparse(told.value).replace("(", "").replace(")", "") == (
        "room_is_up and not session.focused"
    )


def _posted(tree) -> list[str]:
    return [ast.unparse(node) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "posts.post"]


def test_the_controllers_reach_the_pictures_own_controls_and_the_worker():
    tree = ast.parse(inspect.getsource(player._run))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    (made,) = [call for call in calls if ast.unparse(call.func) == "Pointer"]
    (closing,) = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "closing"]

    assert made.keywords == []
    assert "posts.post(thumb.commands)" in _posted(tree)
    assert "posts" in ast.unparse(closing.value)


def test_the_room_hears_from_the_headset_whether_anyone_is_wearing_it():
    tree = ast.parse(inspect.getsource(player._run))

    assert "posts.post(wear.said(session.focused, now=time.monotonic()))" in _posted(tree)


def test_a_carry_turns_the_room_whatever_the_main_player_is_showing():
    """It used to turn the scene only while a video wrapped the viewer, and move
    the main player's own placement otherwise; every carry turns the room now."""
    tree = ast.parse(inspect.getsource(player._run))
    carries = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call) and ast.unparse(node.func) == "carried_heading"]
    conditions = [ast.unparse(node.test) for node in ast.walk(tree) if isinstance(node, ast.If)
                  for inner in ast.walk(node) if inner in carries]

    assert len(carries) == 1
    assert not [test for test in conditions if "immersive" in test or "wrapped" in test]


def test_only_frames_a_worn_headset_took_count_towards_the_dwell():
    """A frame submitted while the runtime cannot locate the views, or while the
    headset is on the desk, showed nobody anything."""
    tree = ast.parse(inspect.getsource(player._run))
    (note,) = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and ast.unparse(n.func) == "cover_seen.note"]

    assert ast.unparse(note) == "cover_seen.note(covered and session.focused)"


def test_the_headset_session_runs_ahead_of_background_work():
    tree = ast.parse(inspect.getsource(player.main))
    (scheduled,) = [n for n in ast.walk(tree)
                    if isinstance(n, ast.With)
                    and [ast.unparse(item.context_expr) for item in n.items]
                    == ["ahead_of_background_work()"]]

    assert ast.unparse(scheduled.body) == "return _run(manifest, vr, args.manifest)"


def test_a_squeeze_brings_forward_what_the_ray_and_the_eyes_both_see():
    """One arrangement goes to the pointer and to the eye pass, so what a squeeze
    lands on is what was in front -- and what it takes hold of is what moves."""
    tree = ast.parse(inspect.getsource(player._run))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    handed = {ast.unparse(call.func): ast.unparse(keyword.value)
              for call in calls for keyword in call.keywords if keyword.arg == "screens"}

    assert handed == {"pointer.frame": "screens", "_draw_eyes": "screens"}
    assert "stacking.take(frame.taken)" in {ast.unparse(call) for call in calls}


def _like(kind, stand_in):
    """Answer the room's questions the way *kind* does, off a stand-in."""
    stand_in.SPOTS = kind.SPOTS
    stand_in.hangings = lambda: kind.hangings(stand_in)
    stand_in.hangs_by = lambda: kind.hangs_by(stand_in)
    stand_in.put_back = lambda: kind.put_back(stand_in)
    return stand_in


_NEVER_DIALED = {"fov_of": lambda _video: None, "height_of": lambda _video: None}


class _AMainStandIn(SimpleNamespace):
    """A stand-in with the main unit's own answers about what fills the slot."""

    wraps_the_viewer = _MainUnit.wraps_the_viewer
    picture_in_the_slot = _MainUnit.picture_in_the_slot


def _a_main_slot(*, picture=True, projection=FLAT, showing=False, clip=True,
                 clip_projection=FLAT, video=None, fov=None, height=None, verbs=None):
    """The main slot as the room reads it: the video or Genau's picture in it."""
    return _like(_MainUnit, _AMainStandIn(
        target=SimpleNamespace(ready=picture, aspect=16 / 9, video=video),
        verbs=verbs or SimpleNamespace(
            projection_of=lambda _video: projection,
            fov_of=lambda _video: fov, height_of=lambda _video: height),
        funestra=SimpleNamespace(playback=SimpleNamespace(current_video=Path(video or "v.mp4"))),
        users_picture_texture=SimpleNamespace(ready=clip, aspect=4 / 3),
        _clip_projection=(Path("clip.mp4"), clip_projection),
        screen=SimpleNamespace(placement=SPOTS[MAIN]),
        owns_the_slot=not showing,
    ))


class TestTheMainPlayersPictureIsWrappedAsItsOwnVideo:
    """The last video's picture stays up while the next video opens, so it is
    wrapped the way its own video is, not the way the next one will be."""

    WIDE, FLAT_VIDEO = "C:/videos/wide.mp4", "C:/videos/flat.mp4"

    def _showing(self, video):
        return _a_main_slot(video=video, verbs=SimpleNamespace(
            projection_of={self.WIDE: EQUIRECT_180_SBS, self.FLAT_VIDEO: FLAT}.get,
            **_NEVER_DIALED))

    def test_a_vr_videos_picture_stays_round_the_viewer_while_a_flat_one_opens(self):
        (hanging,) = self._showing(self.WIDE).hangings()

        assert hanging.wrap == immersive_wrap(EQUIRECT_180_SBS)

    def test_a_flat_videos_picture_stays_on_its_screen_while_a_vr_one_opens(self):
        (hanging,) = self._showing(self.FLAT_VIDEO).hangings()

        assert hanging.wrap is None


class TestDialingTheMainPlayersWrap:
    VIDEO = "C:/videos/wide.mp4"

    def _main_unit(self, *, projection=FISHEYE_180_SBS, fov=None, height=None, showing=False):
        dialed = {}
        verbs = SimpleNamespace(
            projection_of=lambda _video: projection,
            fov_of=lambda _video: dialed.get("fov", fov),
            height_of=lambda _video: dialed.get("height", height),
            set_fov=lambda degrees: dialed.update(fov=degrees),
            set_height=lambda value: dialed.update(height=value),
        )
        main_unit = _a_main_slot(video=self.VIDEO, verbs=verbs, showing=showing)
        main_unit.target.aspect = 1.0
        main_unit.dial_the_wrap = lambda **dials: _MainUnit.dial_the_wrap(main_unit, **dials)
        return main_unit, dialed

    def test_a_picture_is_drawn_at_the_field_of_view_and_height_dialed_for_its_video(self):
        main_unit, _ = self._main_unit(fov=140.0, height=1.2)

        (hanging,) = main_unit.hangings()

        assert hanging.wrap == immersive_wrap(FISHEYE_180_SBS, fov_deg=140.0, height=1.2)

    def test_a_wrapped_picture_can_be_dialed_but_never_a_flat_one_or_the_360(self):
        for projection, dialed in ((FISHEYE_180_SBS, True), (EQUIRECT_180_SBS, True),
                                   (RECTILINEAR_SBS, True), (EQUIRECT_360, False), (FLAT, False)):
            main_unit, _ = self._main_unit(projection=projection)

            assert _MainUnit.can_dial_the_wrap.fget(main_unit) is dialed, projection

    def test_the_picture_is_left_alone_while_a_clip_holds_the_slot(self):
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

    def test_the_frame_loop_hands_the_sticks_and_the_keyboard_to_the_wrap(self):
        loop = inspect.getsource(player._run)

        assert "dialing=dialing" in loop
        assert "zoom = main_unit.verbs.angle_asked.take()" in loop
        assert "main_unit.dial_the_wrap(zoom=zoom, stretch=thumb.stretch)" in loop


class TestTheReadoutOfAWrappedPicture:
    VIDEO = "C:/videos/wide.mp4"

    def _main_unit(self, *, projection=FISHEYE_180_SBS, fov=None, height=None, showing=False):
        return _a_main_slot(video=self.VIDEO, projection=projection, fov=fov, height=height,
                            showing=showing)

    def test_the_main_player_reads_its_own_wrap(self):
        unit = self._main_unit(fov=158.4, height=1.2)

        assert _MainUnit.wrap_readout.fget(unit) == "Fisheye · 158° · height 1.20"

    def test_it_reads_nothing_while_a_clip_holds_the_slot(self):
        assert _MainUnit.wrap_readout.fget(self._main_unit(showing=True)) is None

    def test_each_new_reading_flashes_over_the_main_screen_and_a_repeat_does_not(self):
        flashed = []
        unit = SimpleNamespace(
            _readout=WrapReadout(), screen_name=MAIN, wrap_readout="Fisheye · 158° · height 1.20",
            _notices=SimpleNamespace(flash=lambda said, **how: flashed.append((said, how))))

        _MainUnit.flash_the_readout(unit, 1.0)
        _MainUnit.flash_the_readout(unit, 2.0)

        assert flashed == [("Fisheye · 158° · height 1.20",
                            {"level": NOTICE, "screen": MAIN, "now": 1.0})]

    def test_the_frame_loop_asks_the_main_player_for_its_reading_every_frame(self):
        assert "main_unit.flash_the_readout(now)" in inspect.getsource(player._run)


class TestTheBannerOverAWrappedPicture:
    def _main_unit(self, *, wraps):
        return SimpleNamespace(
            _notices=SimpleNamespace(banner=lambda _screen: "a banner"), wraps_the_viewer=wraps,
            screen_name=MAIN)

    def test_a_flat_picture_takes_its_banner_into_itself(self):
        assert _MainUnit.banner_into_the_picture(self._main_unit(wraps=False)) == "a banner"

    def test_a_wrapped_picture_takes_none_into_itself(self):
        assert _MainUnit.banner_into_the_picture(self._main_unit(wraps=True)) is None

    def _floating(self, *, wraps, banner):
        unit = SimpleNamespace(
            _main_unit=SimpleNamespace(wraps_the_viewer=wraps),
            _notices=SimpleNamespace(banner=lambda _screen: banner),
            _lock=threading.Lock(), _image=None, _uploaded=None, _painted=None,
            texture=SimpleNamespace(ready=True, texture=object(), aspect=6.0),
            screen=SimpleNamespace(placement=Placement(0.0, 24.0, 1.0)),
        )
        _BannerUnit.pump(unit, None, now=0.0)
        return unit

    def test_it_floats_the_banner_in_front_while_the_picture_wraps_the_viewer(self):
        notice = SimpleNamespace(message="Fisheye · 158° · height 1.20", level=NOTICE)

        unit = self._floating(wraps=True, banner=notice)

        (hanging,) = _BannerUnit.hangings(unit)
        assert hanging.in_front and hanging.blend
        assert hanging.screen.name == BANNER
        assert not hanging.screen.pressable and not hanging.screen.movable
        assert unit._image is not None
        assert unit.screen.placement.width_deg == pytest.approx(
            unit._image.width / player.BANNER_PX_PER_DEG)

    def test_it_hangs_nothing_over_a_flat_picture_which_draws_its_own(self):
        notice = SimpleNamespace(message="Flat", level=NOTICE)

        assert _BannerUnit.hangings(self._floating(wraps=False, banner=notice)) == ()

    def test_it_hangs_nothing_once_the_banner_has_faded(self):
        unit = self._floating(wraps=True, banner=SimpleNamespace(message="Flat", level=NOTICE))

        unit._notices = SimpleNamespace(banner=lambda _screen: None)
        _BannerUnit.pump(unit, None, now=3.0)

        assert _BannerUnit.hangings(unit) == ()

    def test_the_banner_is_one_of_the_room(self):
        assert "banner = _BannerUnit(main_unit, notices)" in inspect.getsource(player._run)
        assert "library, banner, cover]" in inspect.getsource(player._run)


class TestTheMainSlotUnderThePointer:
    """The main player moves and zooms by the same handles the satellites do, so
    it is one of the screens the pointer is handed — but only while what fills
    the slot is a flat screen with edges to take hold of."""

    def _units(self, **overrides):
        return (_a_main_slot(**overrides),)

    def _slot(self, **overrides):
        hangings = room.what_hangs(self._units(**overrides))
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

    def test_a_flat_main_player_is_a_picture_a_squeeze_clicks_or_carries(self):
        assert self._slot().picture

    def test_genaus_clip_is_what_the_pointer_finds_there_while_it_has_the_scene(self):
        """It hangs in the same slot, at its own shape — so the handles stay
        under the hand through a switch into kino mode and back."""
        screen = self._slot(showing=True)

        assert screen.name == MAIN
        assert screen.aspect == 4 / 3

    def test_genaus_clip_is_offered_at_the_width_it_hangs_at_to_keep_the_slots_area(self):
        screen = self._slot(showing=True)

        assert screen.placement == shown_at(MAIN, SPOTS[MAIN], 4 / 3)
        assert widened(SPOTS[MAIN], screen.widened_by) == screen.placement

    @pytest.mark.parametrize("state", [
        {"projection": EQUIRECT_180_SBS},
        {"showing": True, "clip_projection": EQUIRECT_180_SBS},
    ])
    def test_a_wrapped_video_has_no_edges_to_take_hold_of(self, state):
        """It is round the viewer rather than hanging in the slot, so there is
        nothing to grab — but it is still what a squeeze out there lands on, so
        it stays in the scene as the screen with no rectangle."""
        screen = self._slot(**state)

        assert (screen.movable, screen.resizable) == (False, False)
        assert (screen.pressable, screen.immersive) == (True, True)

    def test_a_wrapped_video_goes_round_the_viewer_rather_than_on_a_quad(self):
        (wrap,) = room.what_hangs(self._units(projection=EQUIRECT_180_SBS))

        assert wrap.mesh is None
        assert wrap.wrap == immersive_wrap(EQUIRECT_180_SBS)

    @pytest.mark.parametrize("state", [
        {"picture": False},
        {"showing": True, "clip": False},
    ])
    def test_a_slot_with_no_picture_in_it_is_not_in_the_scene_at_all(self, state):
        """Still waiting for its first frame, or standing aside for the other
        player: there is nothing there for the ray to find."""
        assert self._slot(**state) is None

    def test_a_drag_on_the_slot_moves_genaus_picture_with_the_video(self):
        """They share it, so a drag while one is showing must not leave the
        other hanging where the slot used to be."""
        main_unit = _a_main_slot()

        room.where_they_hang([main_unit])[MAIN].put(_UNDER_THE_DASH)

        assert main_unit.screen.placement == _UNDER_THE_DASH
        assert self._slot(showing=True).placement == shown_at(MAIN, SPOTS[MAIN], 4 / 3)

    def _the_room_around_the_slot(self) -> list:
        dash = _like(_DashUnit, SimpleNamespace(
            texture=SimpleNamespace(ready=False, aspect=2.5),
            screen=SimpleNamespace(placement=SPOTS[DASH])))
        reference = _like(_ReferenceUnit, SimpleNamespace(
            showing=False, texture=SimpleNamespace(ready=False, aspect=1.7),
            screen=SimpleNamespace(placement=_UNDER_THE_DASH)))
        return room.arranged(Stacking(), room.what_hangs(
            [*self._units(), _a_satellite(LANDSCAPE), _a_panel(), dash, reference]))

    def test_the_main_slot_is_listed_under_the_screens_that_overlap_it(self):
        """It is drawn first and it is the biggest, so a satellite tucked over its
        edge — and the console under them all — has to win the ray.  The console
        is pressed, never dragged: it rides on the main player now."""
        screens = self._the_room_around_the_slot()

        assert [screen.name for screen in screens] == [MAIN, LANDSCAPE, PANEL]
        console = screens[-1]
        assert (console.pressable, console.movable, console.resizable) == (True, False, False)

    def test_a_satellite_is_a_picture_and_the_console_is_not(self):
        screens = {screen.name: screen for screen in self._the_room_around_the_slot()}

        assert screens[LANDSCAPE].picture
        assert not screens[PANEL].picture


class TestGenausPictureInTheSlot:
    """Genau is handed finished pictures rather than decoding its own: the
    frame its engine chose, handed over by the Funestra as a User's own
    picture, is uploaded untouched and wrapped the way its clip is."""

    def _unit(self, *, showing=True, vr_dirs=()):
        unit = _MainUnit.__new__(_MainUnit)
        unit.funestra = SimpleNamespace(showing=MainMode.GENAU if showing else MainMode.KINO)
        unit.users_picture = LatestPicture()
        unit.users_picture_texture = _FakeTexture()
        unit._uploaded_frame = None
        unit._clip_projection = (None, "")
        unit._vr_dirs = vr_dirs
        unit.screen = SimpleNamespace(placement=SPOTS[MAIN],
                                      rehang_at=lambda _placement, _aspect: None)
        return unit

    def _handed(self, unit, clip: Path):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        unit.users_picture.show(Picture(frame=frame, clip=clip), (640, 360))
        unit._show_the_users_picture()
        return frame

    def test_the_frame_is_uploaded_untouched(self):
        unit = self._unit()

        frame = self._handed(unit, Path("C:/clips/VR/alpha_180.mp4"))

        assert unit.users_picture_texture.uploads == [frame]

    def test_a_frame_is_uploaded_once_however_many_frames_the_loop_runs(self):
        unit = self._unit()

        self._handed(unit, Path("C:/clips/VR/alpha_180.mp4"))
        unit._show_the_users_picture()

        assert len(unit.users_picture_texture.uploads) == 1

    def test_a_clip_in_the_vr_folder_wraps_the_viewer_and_a_flat_one_hangs_on_the_screen(self):
        vr = Path("C:/clips/VR")
        unit = self._unit(vr_dirs=(vr,))

        self._handed(unit, vr / "scene one.mp4")
        assert unit._clip_projection[1] == EQUIRECT_180_SBS
        assert unit.hangings()[0].wrap == immersive_wrap(EQUIRECT_180_SBS)

        self._handed(unit, Path("C:/clips/2D/scene two.mp4"))
        assert unit._clip_projection[1] == FLAT
        assert unit.hangings()[0].wrap is None

    def test_nothing_is_uploaded_while_the_video_has_the_slot(self):
        unit = self._unit(showing=False)

        self._handed(unit, Path("C:/clips/VR/alpha_180.mp4"))

        assert unit.users_picture_texture.uploads == []

    def test_nothing_hangs_before_a_frame_has_arrived(self):
        unit = self._unit()

        unit._show_the_users_picture()

        assert unit.hangings() == ()


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


def _hanging(name: str, aspect: float):
    return SimpleNamespace(
        texture=SimpleNamespace(ready=True, texture=object(), aspect=aspect),
        screen=SimpleNamespace(ready=True, mesh=name,
                               placement=SPOTS.get(name, _UNDER_THE_DASH)),
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

    def _draw(self, *, showing: bool = False, browsing: bool = False, projection=None,
              stacking=None):
        renderer = _FakeRenderer()
        session = SimpleNamespace(
            bind_eye_framebuffer=lambda _i: None, release_eye_framebuffer=lambda _i: None)
        main_unit = _a_main_slot(picture=projection is not None, projection=projection or FLAT,
                                 clip=False)
        main_unit.target.texture = object()
        main_unit.screen = SimpleNamespace(ready=True, mesh=MAIN, placement=SPOTS[MAIN])
        panel = _like(_PanelUnit, _hanging(PANEL, 1.2))
        dash = _like(_DashUnit, _hanging(DASH, 2.5))
        reference = _like(_ReferenceUnit, _hanging(REFERENCE, 1.7))
        reference.showing = showing
        library = _like(_LibraryUnit, _hanging(LIBRARY, 16 / 9))
        library.showing = browsing
        hangings = room.what_hangs([main_unit, panel, dash, reference, library])
        screens = room.arranged(stacking or Stacking(), hangings)
        _draw_eyes(
            session, renderer, hangings,
            SimpleNamespace(draw=lambda *_a: None), self._views(), np.eye(4, dtype=np.float64),
            screens=screens, as_quads=set(),
        )
        return renderer

    def test_the_console_reaches_the_eyes(self):
        assert "panel" in self._draw().screens

    @pytest.mark.parametrize("projection", [None, FLAT, EQUIRECT_180_SBS])
    def test_the_dashboard_reaches_them_whichever_video_is_in_the_slot(self, projection):
        assert "dash" in self._draw(projection=projection).screens

    def test_the_reference_reaches_them_while_it_is_up(self):
        assert "reference" in self._draw(showing=True).screens

    def test_and_stays_out_of_them_while_it_is_down(self):
        """It covers the picture, so it is drawn only while it is asked for."""
        assert "reference" not in self._draw(showing=False).screens

    def test_the_browse_reaches_them_while_it_is_up(self):
        assert LIBRARY in self._draw(browsing=True).screens

    def test_the_browse_stays_out_of_them_while_it_is_put_away(self):
        assert LIBRARY not in self._draw(browsing=False).screens

    def test_the_browse_is_drawn_in_front_of_whatever_was_taken_last(self):
        stacking = Stacking()
        stacking.take(PANEL)

        drawn = self._draw(projection=FLAT, browsing=True, stacking=stacking).screens

        assert drawn[-1] == LIBRARY

    def test_they_are_drawn_back_to_front_as_they_stand(self):
        stacking = Stacking()
        stacking.take(PANEL)

        drawn = self._draw(projection=FLAT, stacking=stacking).screens

        assert drawn.index(MAIN) < drawn.index(DASH) < drawn.index(PANEL)

    def test_a_video_wrapped_round_the_viewer_is_drawn_first_however_it_was_taken(self):
        """Taken hold of while it was a flat screen, the wrap is still drawn before them."""
        stacking = Stacking()
        stacking.take(MAIN)

        drawn = self._draw(projection=EQUIRECT_180_SBS, stacking=stacking).screens

        assert drawn[0] == "immersive"
        assert sorted(drawn[1:]) == sorted([PANEL, DASH])



_UNDER_THE_DASH = Placement(azimuth_deg=0.0, elevation_deg=38.0, width_deg=54.0)

class _FakeChromeRenderer:
    """Records the pointer's draws in order: what is drawn later lands on top."""

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
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
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



def _a_panel(*, ready=True):
    """The console as the pointer reads it: pressed, never dragged."""
    return _like(_PanelUnit, SimpleNamespace(
        texture=SimpleNamespace(ready=ready, aspect=1.2),
        screen=SimpleNamespace(placement=SPOTS[PANEL]),
    ))


def _slot(*, wrapped=False):
    """The main slot as the dashboard reads it."""
    return _a_main_slot(projection=EQUIRECT_180_SBS if wrapped else FLAT)


def _a_dash(tmp_path, *, wrapped=False, texture=None, remembered=None):
    with patch("fun_time_vr.player.FrameTexture"):
        dash = _DashUnit(
            _slot(wrapped=wrapped),
            remembered=remembered or {},
            dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            notices=NoticeBoard(tmp_path / "event_log.jsonl"),
            dashboard_state_file=tmp_path / "dashboard_state.ini",
            reference_flag=tmp_path / REFERENCE_OPEN_FILENAME,
        )
    if texture is not None:
        dash.texture = texture
    return dash


class TestTheDashUnderThePointer:
    """It was drawn and it was up to date, and the pointer had never heard of
    it: no button did anything, and there was no handle to move it off the
    picture it was covering."""

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

        unit.point(Frame(events=[PressEvent(PRESS, PANEL, *self._uv_of(QUIT_BUTTON))]))
        unit.pump(threading.Event(), 0.0)

        assert not (tmp_path / "dashboard_cmd.txt").exists()


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
    with patch("fun_time_vr.player.FrameTexture"):
        return _LibraryUnit(
            remembered=remembered or {},
            flag=tmp_path / LIBRARY_OPEN_FILENAME,
            host=host,
            main_player_cmd_file=tmp_path / "main_player_cmd.txt",
            main_player_status_file=tmp_path / "main_player_status.txt",
            genau_cmd_file=tmp_path / "genau_cmd.txt",
            genau_status_file=tmp_path / "genau_status.txt",
            genau_has_the_slot=lambda: genau_has_the_slot,
            dashboard_cmd_file=tmp_path / "dashboard_cmd.txt",
            metadata_root=metadata_root,
        )


def _a_frame(value: int = 7) -> tuple[int, int, bytes]:
    width, height = LIBRARY_SIZE_PX
    return width, height, bytes([value]) * (width * height * 4)


def _uv(x: int, y: int) -> tuple[float, float]:
    width, height = LIBRARY_SIZE_PX
    return (x + 0.5) / width, 1 - (y + 0.5) / height


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

    def test_a_video_picked_plays_on_the_main_player_and_puts_the_browse_away(self, tmp_path):
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

    def test_with_genau_in_the_main_slot_it_opens_genaus_clips_on_the_clip_up(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host, genau_has_the_slot=True)
        (tmp_path / "genau_status.txt").write_text(
            "clip=C:/clips/Loop One.mp4\n", encoding="utf-8")
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)

        unit.pump(threading.Event(), 0.0)

        assert host.sent == ["clips 1 C:/clips/Loop One.mp4"]

    def test_a_clip_picked_plays_on_genau_and_puts_the_browse_away(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host, genau_has_the_slot=True)
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)

        host.said.append("picked C:/clips/Loop Two.mp4")
        unit.pump(threading.Event(), 0.0)

        assert (tmp_path / "genau_cmd.txt").read_text(encoding="utf-8").strip() == (
            f"PLAY_FILE {Path('C:/clips/Loop Two.mp4')}")
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
        unit.texture = _FakePanelTexture()
        host.frames[1] = _a_frame()
        unit.pump(threading.Event(), 0.0)
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        assert not hasattr(unit.texture, "uploaded")

        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
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

    def test_the_stick_on_it_scrolls_it_and_sizes_no_player(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        host.frames[1] = _a_frame()
        write_flag(tmp_path / LIBRARY_OPEN_FILENAME, True)
        unit.pump(threading.Event(), 0.0)
        unit.point(Frame(hover=Hover(LIBRARY, SURFACE, *_uv(300, 400))))
        host.sent.clear()

        hands = _hands_for_the_players(
            unit, {"left": HandInput(stick_y=0.2), "right": HandInput(stick_y=-1.0, forward=True)},
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

        hands = _hands_for_the_players(unit, {"right": HandInput(stick_x=0.9)}, elapsed_s=0.5)

        assert hands == {"right": HandInput()}

    def test_the_stick_elsewhere_is_the_players_to_size(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)
        hands = {"right": HandInput(stick_y=-1.0)}

        assert _hands_for_the_players(unit, hands, elapsed_s=0.5) == hands
        assert host.sent == []

    def test_closing_the_session_ends_its_browser(self, tmp_path):
        host = _FakeLibraryHost()
        unit = _a_library(tmp_path, host)

        unit.close()

        assert host.closed


class TestWhatThePointerCanReach:
    def _screens(self, tmp_path, *, reference_showing=False, wrapped=False):
        panel = _a_panel()
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
            [main_unit, panel, dash, reference]))}

    def test_the_dash_is_one_of_them(self, tmp_path):
        assert DASH in self._screens(tmp_path)

    def test_the_reference_joins_them_only_while_it_is_up(self, tmp_path):
        """Nothing to point at on a panel that is not there, and its handle
        would sit in the scene with no panel under it."""
        assert REFERENCE not in self._screens(tmp_path)
        assert REFERENCE in self._screens(tmp_path, reference_showing=True)

    def test_it_can_be_pressed_and_dragged(self, tmp_path):
        """Movable is what gives a screen the bar it is dragged by; without it
        there was no way to get it off what it was covering.  The console beside
        it is pressed and never dragged -- it rides on what is above it."""
        screens = self._screens(tmp_path)

        assert screens[DASH].pressable and screens[DASH].movable
        assert screens[PANEL].pressable and not screens[PANEL].movable

    def test_the_reference_is_pressed_but_offers_no_handles_of_its_own(self, tmp_path):
        """It moves with the dashboard it hangs from."""
        reference = self._screens(tmp_path, reference_showing=True)[REFERENCE]

        assert reference.pressable
        assert not reference.movable and not reference.resizable

    def test_the_dashboard_keeps_the_only_handle_when_it_carries_the_console(self, tmp_path):
        """It is the top of the pair then, so its bar is above both of them --
        a bar on the console would sit between them instead."""
        screens = self._screens(tmp_path, wrapped=True)

        assert screens[DASH].movable and not screens[DASH].resizable
        assert screens[PANEL].pressable and not screens[PANEL].movable


def _a_satellite(player: str, *, hud: bool = False):
    placement = SPOTS[player]
    return _like(_SatelliteUnit, SimpleNamespace(
        screen_name=player, target=SimpleNamespace(ready=True, aspect=16 / 9),
        screen=SimpleNamespace(placement=placement), hud_ready=hud,
        hud_screen=SimpleNamespace(placement=attached_below(
            placement, aspect=16 / 9, width_deg=20.0, hanging_aspect=6.0)),
        hud_texture=SimpleNamespace(aspect=6.0),
    ))


class TestWhatComesForwardTogether:
    def _arranged(self, stacking, satellites, *, panel=None, dash=None, reference_up=False,
                  wrapped=False):
        dash = dash or _like(_DashUnit, SimpleNamespace(
            texture=SimpleNamespace(ready=False, aspect=2.5),
            screen=SimpleNamespace(placement=SPOTS[DASH])))
        reference = _like(_ReferenceUnit, SimpleNamespace(
            showing=reference_up, texture=SimpleNamespace(ready=True, aspect=1.7),
            screen=SimpleNamespace(placement=_UNDER_THE_DASH)))
        return [screen.name for screen in room.arranged(stacking, room.what_hangs(
            [_slot(wrapped=wrapped), *satellites,
             panel or _a_panel(ready=False), dash, reference]))]

    def test_the_reference_comes_forward_as_part_of_the_dashboard(self):
        stacking = Stacking()
        dash = _like(_DashUnit, SimpleNamespace(
            texture=SimpleNamespace(ready=True, aspect=2.5),
            screen=SimpleNamespace(placement=SPOTS[DASH])))
        stacking.take(MAIN)
        stacking.take(REFERENCE)

        assert self._arranged(stacking, [], dash=dash, reference_up=True) == [
            MAIN, DASH, REFERENCE]

    def test_a_satellites_hud_brings_its_picture_forward_with_it(self):
        stacking = Stacking()
        stacking.take(MAIN)
        stacking.take(hud_screen_name(LANDSCAPE))

        assert self._arranged(stacking, [_a_satellite(LANDSCAPE, hud=True)]) == [
            MAIN, LANDSCAPE, hud_screen_name(LANDSCAPE)]

    def test_the_console_comes_forward_with_the_main_player_it_hangs_under(self):
        stacking = Stacking()
        stacking.take(LANDSCAPE)
        stacking.take(MAIN)

        assert self._arranged(stacking, [_a_satellite(LANDSCAPE)], panel=_a_panel()) == [
            LANDSCAPE, MAIN, PANEL]

    def test_the_dashboard_brings_no_console_forward_while_a_video_wraps_the_viewer(self, tmp_path):
        """The console hangs from it then, but what stands in front is the dashboard's
        own business, the same as every other screen's."""
        stacking = Stacking()
        dash = _a_dash(tmp_path, wrapped=True, texture=SimpleNamespace(ready=True, aspect=2.5))
        stacking.take(LANDSCAPE)
        stacking.take(DASH)

        assert self._arranged(stacking, [_a_satellite(LANDSCAPE)], panel=_a_panel(), dash=dash,
                              wrapped=True) == [MAIN, PANEL, LANDSCAPE, DASH]


class TestTheReferenceUnderTheDashboard:
    """The hotkeys and voice reference as a part of the dashboard: it hangs from
    it and goes wherever it goes, with no handles of its own."""

    def _unit(self, tmp_path, *, carrying_the_console=False):
        dash = SimpleNamespace(screen=SimpleNamespace(placement=SPOTS[DASH]),
                               texture=SimpleNamespace(aspect=2.5),
                               carrying_the_console=carrying_the_console)
        panel = SimpleNamespace(
            screen=SimpleNamespace(placement=Placement(0.0, 20.0, PANEL_WIDTH_DEG)),
            texture=SimpleNamespace(aspect=1.2))
        with patch("fun_time_vr.player.FrameTexture", _FakePanelTexture):
            unit = _ReferenceUnit(dash, panel, flag=tmp_path / REFERENCE_OPEN_FILENAME)
        return unit, dash, panel

    @staticmethod
    def _placed(unit) -> Placement:
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            unit.render_latest_frame()
        return unit.screen.placement

    @staticmethod
    def _meets(upper: Placement, upper_aspect: float, lower: Placement) -> bool:
        above = surface_vertices(upper, aspect=upper_aspect)
        below = surface_vertices(lower, aspect=_FakePanelTexture.aspect)
        return below[:, 1].max() == pytest.approx(above[:, 1].min(), abs=1e-6)

    def test_it_hangs_from_the_dashboard_wherever_the_dashboard_goes(self, tmp_path):
        unit, dash, _panel = self._unit(tmp_path)

        under = self._placed(unit)
        dash.screen.placement = Placement(azimuth_deg=-40.0, elevation_deg=30.0, width_deg=40.0)
        followed = self._placed(unit)

        assert under.azimuth_deg == SPOTS[DASH].azimuth_deg
        assert self._meets(SPOTS[DASH], 2.5, under)
        assert followed.azimuth_deg == -40.0
        assert self._meets(dash.screen.placement, 2.5, followed)
        assert under.width_deg == followed.width_deg == REFERENCE_WIDTH_DEG

    def test_it_hangs_below_the_console_while_the_console_hangs_from_the_dashboard(
            self, tmp_path):
        """A wrapped video leaves the console nowhere else to dock."""
        unit, _dash, panel = self._unit(tmp_path, carrying_the_console=True)

        placed = self._placed(unit)

        assert placed.azimuth_deg == panel.screen.placement.azimuth_deg
        assert self._meets(panel.screen.placement, 1.2, placed)


class TestWhereTheDashboardHangs:
    """Two remembered spots: its own above the main player, and the one it takes
    with the console under it -- dragged there, it must not drop over the
    picture the next time a flat video comes back."""

    def _placed(self, tmp_path, *, wrapped=False, remembered=None):
        dash = _a_dash(tmp_path, wrapped=wrapped, texture=_FakePanelTexture(),
                       remembered=remembered)
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()
        return dash

    def test_it_keeps_its_own_place_while_a_picture_is_in_the_slot(self, tmp_path):
        """A flat video's dashboard is where the session left it, and where it
        always was: the console docking under it is the wrapped case alone."""
        assert self._placed(tmp_path).screen.placement == SPOTS[DASH]

    def test_a_flat_video_does_not_move_it_with_the_console(self, tmp_path):
        """The console rides under the picture there, and the dashboard rides
        nothing: dragging the player must leave the dashboard alone."""
        dash = self._placed(tmp_path)

        dash._main_unit.screen.placement = Placement(
            azimuth_deg=-40.0, elevation_deg=-25.0, width_deg=90.0)
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()

        assert dash.screen.placement == SPOTS[DASH]

    def test_a_flat_video_still_offers_it_to_the_pointer(self, tmp_path):
        """Drawn but unreachable is the same as gone: it is the only thing in
        the room carrying the buttons a controller can press."""
        screens = TestWhatThePointerCanReach()._screens(tmp_path)

        assert screens[DASH].pressable and screens[DASH].movable

    def test_it_takes_the_pairs_place_while_it_carries_the_console(self, tmp_path):
        assert self._placed(tmp_path, wrapped=True).screen.placement == SPOTS[PANEL]

    def test_a_drag_lands_in_the_spot_that_is_showing(self, tmp_path):
        put = Placement(azimuth_deg=25.0, elevation_deg=-12.0, width_deg=40.0)
        dash = self._placed(tmp_path, wrapped=True)

        assert dash.layout_key == PANEL
        dash.placement = put  # what the frame loop does with a settled drag
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()

        assert dash.screen.placement == put

    def test_the_other_spot_is_untouched_by_it(self, tmp_path):
        """Dragged down over a wrapped video, it must not be down there over the
        picture when the next flat one comes up."""
        dash = self._placed(tmp_path, wrapped=True)

        dash.placement = Placement(azimuth_deg=25.0, elevation_deg=-12.0, width_deg=40.0)

        assert dash._floating == SPOTS[DASH]
        assert dash.layout_key == PANEL

    def test_it_opens_in_whichever_of_the_two_the_last_session_left_it_in(self, tmp_path):
        """Both are remembered, so a dashboard dragged in one of them must not
        be found in the other's place the next time that one comes up."""
        floating = Placement(azimuth_deg=-30.0, elevation_deg=40.0, width_deg=44.0)
        carrying = Placement(azimuth_deg=15.0, elevation_deg=-20.0, width_deg=36.0)
        remembered = {DASH: floating, PANEL: carrying}

        assert self._placed(tmp_path, remembered=remembered).screen.placement == floating
        assert self._placed(
            tmp_path, wrapped=True, remembered=remembered).screen.placement == carrying

    def test_a_vr_reset_puts_both_of_its_spots_back_wherever_it_was_dragged(self, tmp_path):
        dash = self._placed(tmp_path)
        dash.placement = Placement(azimuth_deg=110.0, elevation_deg=5.0, width_deg=40.0)

        dash.put_back()
        with patch("fun_time_vr.player.ScreenMesh", _FakeMesh):
            dash.render_latest_frame()

        assert dash.screen.placement == SPOTS[DASH]
        assert (dash._floating, dash._wrapped) == (SPOTS[DASH], SPOTS[PANEL])


_DRAGGED_OFF = Placement(azimuth_deg=-110.0, elevation_deg=-30.0, width_deg=15.0)


class TestTheSpotEachScreenStartsIn:
    """No one table lists these: the unit that hangs a screen keeps the spot it
    opens in, so a screen is added by writing its unit and registering it."""

    def test_the_main_screen_is_one_of_the_movable_screens_hanging_dead_ahead(self):
        """It moves and zooms by the same handles the satellites do, and starts
        where it has always sat -- level and straight on."""
        assert _MainUnit.SPOTS[MAIN] == Placement(0.0, 0.0, MAIN_WIDTH_DEG)

    def test_the_satellites_flank_the_main_screen_landscape_left_portrait_right(self):
        """The sides a desktop session puts them on, so the room reads the same
        in the headset as it does on the monitors."""
        portrait, landscape = _SatelliteUnit.SPOTS[PORTRAIT], _SatelliteUnit.SPOTS[LANDSCAPE]

        assert landscape.azimuth_deg < 0 < portrait.azimuth_deg
        assert portrait.azimuth_deg == -landscape.azimuth_deg
        assert portrait.width_deg == landscape.width_deg
        assert portrait.elevation_deg == landscape.elevation_deg

    def test_the_satellites_tuck_inside_the_flush_position(self):
        # First headset run: flush-beside-the-main-player put both satellites in
        # the peripheral vision, so they overlap the main player's edges instead --
        # they draw over it, so overlap costs nothing.
        landscape = _SatelliteUnit.SPOTS[LANDSCAPE]
        flush = (MAIN_WIDTH_DEG + landscape.width_deg) / 2

        assert abs(landscape.azimuth_deg) < flush

    def test_the_satellites_are_smaller_than_half_the_main_screen(self):
        assert _SatelliteUnit.SPOTS[LANDSCAPE].width_deg < MAIN_WIDTH_DEG / 2

    def test_the_satellites_ride_above_the_horizon(self):
        assert _SatelliteUnit.SPOTS[LANDSCAPE].elevation_deg > 0

    def test_only_the_screens_a_controller_places_keep_one(self):
        """The console is one of them: a video that wraps the viewer leaves no
        picture to dock it under, so the dashboard carries it and keeps a second
        spot for that.  The reference is not -- it hangs from the dashboard,
        wherever that was put."""
        assert set(SPOTS) == {MAIN, PORTRAIT, LANDSCAPE, PANEL, DASH, LIBRARY}

    def test_every_one_of_them_is_already_inside_the_scene(self):
        """A spot outside the reach a drag is held to would be clamped the first
        time it was written down, so the screen would not come back where it
        opened."""
        for name, spot in SPOTS.items():
            assert clamp_placement(spot) == spot, name


class TestPuttingTheRoomBack:
    """One press puts every screen back: each unit hangs its own in the spot it
    keeps, and the session is left remembering nothing -- which is what makes
    the next one open the same way."""

    @pytest.mark.parametrize("player", [PORTRAIT, LANDSCAPE])
    def test_a_satellite_goes_back_to_its_own_side(self, player):
        unit = _like(_SatelliteUnit, SimpleNamespace(
            screen_name=player, screen=SimpleNamespace(placement=_DRAGGED_OFF)))

        unit.put_back()

        assert unit.screen.placement == _SatelliteUnit.SPOTS[player]

    def test_the_main_player_goes_back_to_the_slot(self):
        unit = _like(_MainUnit, SimpleNamespace(
            screen_name=MAIN, screen=SimpleNamespace(placement=_DRAGGED_OFF)))

        unit.put_back()

        assert unit.screen.placement == _MainUnit.SPOTS[MAIN]

    def test_the_browse_goes_back_to_its_own_spot(self, tmp_path):
        unit = _a_library(tmp_path, _FakeLibraryHost())
        unit.screen.placement = _DRAGGED_OFF

        unit.put_back()

        assert unit.screen.placement == _LibraryUnit.SPOTS[LIBRARY]

    @pytest.mark.parametrize("kind", [_PanelUnit, _ReferenceUnit, _CoverUnit])
    def test_what_rides_on_another_screen_keeps_no_spot_and_is_left_riding(self, kind):
        """Each of these hangs off something else and finds its place again every
        frame, so putting the room back must not pull it off what it rides on."""
        riding = SimpleNamespace(screen=SimpleNamespace(placement=_DRAGGED_OFF))

        kind.put_back(riding)

        assert kind.SPOTS == {}
        assert riding.screen.placement == _DRAGGED_OFF


class TestWhereItHangsToStart:
    def test_the_dash_opens_centered_over_the_main_player(self):
        assert SPOTS[DASH].azimuth_deg == SPOTS[MAIN].azimuth_deg

    def test_it_opens_two_reposition_handles_above_the_main_players_top(self):
        main_top = surface_vertices(SPOTS[MAIN], aspect=16 / 9)[:, 1].max()
        dash_lower_edge = surface_vertices(
            SPOTS[DASH], aspect=DASH_WIDTH_PX / dash_height())[:, 1].min()

        assert dash_lower_edge - main_top == pytest.approx(
            2 * RADIUS * np.radians(HANDLE_DEG), rel=0.05)
