from __future__ import annotations

import ast
import contextlib
import dataclasses
import inspect
import json
import logging
import os
import subprocess
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app_support.testing import mutex_free
from funestra.modes import MainMode

from fun_time import checkout_overrides, funestra_deaths, win32_taskbar, windows_bridge_startup
from fun_time import orchestrator as desktop_orchestrator
from fun_time.config import load_config
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_deaths import origenerator_never_came_up_message
from fun_time.genau_config import GenauSettings
from fun_time.headset_power import OUR_RUNTIME_NAME
from fun_time.hosted_origenerator import HostedApp
from fun_time.manifest import LaunchManifest, build_windows_bridge_manifest, write_manifest_data
from fun_time.mode_plan import show_verb
from fun_time.modes import VideoShapes
from fun_time.origenerator_opening import OrigeneratorNeverCameUp
from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    CANCELING,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    SHUTDOWN_READY_FILENAME,
    STARTING_VOICE_COMMANDS,
    NullProgress,
    PhaseProgress,
    StartupCancelled,
    parse_progress,
)
from fun_time.project_paths import (
    HOTKEY_SCRIPT_NAME,
    PROJECT_DIR,
    SIBLING_CHECKOUTS_OVERRIDE_NAME,
)
from fun_time.session_end import SESSION_END_MARKER
from fun_time.session_environment import SessionEnvironment
from fun_time.session_handoff import (
    DESKTOP,
    VR,
    crossing_progress_path,
    headset_hold_asked,
    hold_the_headset,
    pending_handoff,
    raise_transition_screen,
    request_handoff,
    take_handoff_request,
)
from fun_time.shared_state import (
    BridgeState,
    SatelliteState,
    read_shared_state,
    shared_state_path,
    write_shared_state,
)
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time.windows_bridge_orchestrator import ChildProcess
from fun_time_vr import app, orchestrator, vr_runtime
from fun_time_vr.bringup import SESSION_BRINGUP_TIMEOUT_S
from fun_time_vr.funestras import VrSettings
from fun_time_vr.loading_screen import (
    CLOSING_STATUS,
    SHUTDOWN_STALE_TIMEOUT_S,
    STARTUP_STALE_TIMEOUT_S,
    scene_ready_file,
)
from fun_time_vr.orchestrator import (
    VR_FUNESTRAS_MODULE,
    _closing_screen,
    _LoadingScreen,
    _release_vr_runtime,
    _wait_for_funestra,
    _wait_for_session_end,
    _wait_for_the_headset_hold,
    _wait_for_the_room,
    build_vr_manifest,
    launch_vr_funestras,
    resume_genau_shapes,
    stock_the_playlists,
    validate_vr_config,
    vr_main_sources,
)
from tests.hosted_shows import an_app_that_opens_each_slideshow_asked_of_it
from tests.loading_screen_beats import heard_from_again_once_stale
from tests.made_up_genau import made_up_genau, point_the_shared_settings_at


@pytest.fixture
def config(tmp_path, monkeypatch):
    library = tmp_path / "library"
    vr_dir = library / "VR" / "finished"
    flat_dir = library / "2D"
    for directory in (vr_dir, flat_dir, tmp_path / "trash"):
        directory.mkdir(parents=True)
    ahk = tmp_path / "AutoHotkey64.exe"
    python = tmp_path / "python.exe"
    ahk.write_bytes(b"")
    python.write_bytes(b"")
    config_path = tmp_path / "fun_time_config.json"
    config_path.write_text(
        """
        {{
          "paths": {{
            "ahk_exe": "{ahk}",
            "python_exe": "{python}",
            "main_player_library_dirs": ["{flat}"],
            "state_dir": "{state}"
          }},
          "layout": {{
            "primary_monitor": 1, "secondary_monitor": 2,
            "main_top_ratio": 0.7, "landscape_width_ratio": 0.6
          }},
          "audio_companion": {{"host": "127.0.0.1", "port": 50556}},
          "vr": {{
            "library_dirs": ["{vr}"],
            "audio_device": "Example Headset"
          }}
        }}
        """.format(
            ahk=str(ahk).replace("\\", "/"),
            python=str(python).replace("\\", "/"),
            flat=str(flat_dir).replace("\\", "/"),
            state=str(tmp_path / "state").replace("\\", "/"),
            vr=str(vr_dir).replace("\\", "/"),
        ),
        encoding="utf-8",
    )
    point_the_shared_settings_at(monkeypatch, made_up_genau(tmp_path))
    return load_config(config_path)


class TestVrConfig:
    def test_vr_section_loads(self, config, tmp_path):
        assert config.vr.library_dirs == (tmp_path / "library" / "VR" / "finished",)
        assert config.vr.audio_device == "Example Headset"
        assert config.vr.compositor_layers is False

    def test_absent_vr_section_defaults_empty(self, config, tmp_path):
        raw = (tmp_path / "fun_time_config.json").read_text(encoding="utf-8")
        stripped = raw[: raw.rindex(',\n          "vr"')] + "\n        }"
        bare_path = tmp_path / "bare_config.json"
        bare_path.write_text(stripped, encoding="utf-8")
        bare = load_config(bare_path)
        assert bare.vr.library_dirs == ()
        assert bare.vr.audio_device is None

    def test_validate_rejects_a_missing_vr_dir(self, config, tmp_path):
        (tmp_path / "library" / "VR" / "finished").rmdir()
        with pytest.raises(FileNotFoundError):
            validate_vr_config(config)


class TestVrManifest:
    def test_primary_sources_merge_vr_first_then_the_desktop_dirs(self, config, tmp_path):
        spec = vr_main_sources(config)
        assert spec.split("|") == [
            str(tmp_path / "library" / "VR" / "finished"),
            str(tmp_path / "library" / "2D"),
        ]

    def test_every_reader_of_the_manifest_sees_the_vr_rotation_as_the_main_sources(self, config):
        manifest = build_vr_manifest(config)

        assert manifest["media"]["main_funestra_library_sources"] == vr_main_sources(config)

    def test_the_manifest_marks_the_vr_half_of_those_sources_for_narrowing_by_shape(self, config):
        media = build_vr_manifest(config)["media"]

        vr_half = media["vr_library_dirs"].split("|")
        assert vr_half == [str(config.vr.library_dirs[0])]
        assert set(vr_half) < set(media["main_funestra_library_sources"].split("|"))

    def test_the_manifest_adds_the_vr_section(self, config):
        vr = build_vr_manifest(config)["vr"]
        assert vr["funestra_module"] == VR_FUNESTRAS_MODULE
        assert vr["library_dirs"] == str(config.vr.library_dirs[0])
        assert vr["audio_device"] == "Example Headset"
        assert vr["compositor_layers"] == "0"

    def test_every_vr_key_the_writer_emits_has_a_field_to_land_in(self, config):
        written = set(build_vr_manifest(config)["vr"])
        read_back = {field.name for field in fields(VrSettings)}
        genau_keys = set(GenauSettings().manifest_fields())
        started_from_the_launchers_own_constant = {"funestra_module"}

        assert written - started_from_the_launchers_own_constant - genau_keys == (
            read_back - {"genau"})
        assert genau_keys <= written

    def test_manifest_carries_a_layers_opt_in(self, config):
        opted_in = dataclasses.replace(
            config, vr=dataclasses.replace(config.vr, compositor_layers=True)
        )
        assert build_vr_manifest(opted_in)["vr"]["compositor_layers"] == "1"

    def test_everything_else_is_the_desktop_manifest(self, config):
        manifest = build_vr_manifest(config)
        assert manifest["modules"]["satellite_module"] == "satellite"
        assert Path(manifest["commands"]["main_funestra_cmd_file"]).name == "main_player_cmd.txt"


class TestOrigeneratorInVr:
    @pytest.fixture
    def hosted(self, config, tmp_path):
        origenerator = tmp_path / "origenerator"
        origenerator.mkdir()
        return replace(config, paths=replace(
            config.paths,
            origenerator_dir=origenerator,
            origenerator_python_exe=tmp_path / "system_python.exe",
        ))

    def test_the_desktop_manifest_does_name_one(self, hosted, tmp_path):
        desktop = build_windows_bridge_manifest(hosted)
        assert desktop["runtime"]["origenerator_dir"] == str(tmp_path / "origenerator")
        assert desktop["executables"]["origenerator_python_exe"]

    def test_the_vr_manifest_names_the_same_checkout(self, hosted, tmp_path):
        manifest = build_vr_manifest(hosted)

        assert manifest["runtime"]["origenerator_dir"] == str(tmp_path / "origenerator")
        assert manifest["executables"]["origenerator_python_exe"]

    def test_the_session_reads_back_as_hosting_one(self, hosted, tmp_path):
        path = write_manifest_data(build_vr_manifest(hosted), tmp_path / "launch.ini")
        bridge = build_bridge_config_from_manifest(
            LaunchManifest.read(path), vr_main_funestra=True)

        assert bridge.origenerator_enabled is True

    def test_the_hosted_app_whose_boot_is_slowest_starts_before_the_funestra(self):
        calls = _call_lines_in_run_vr_bridge()

        assert calls["bring_up_the_hosted_app"] < calls["launch_vr_funestras"]

    def test_the_app_can_import_the_checkouts_this_session_names(self):
        tree = ast.parse(_the_session_as_one_source())
        (call,) = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and ast.unparse(n.func) == "bring_up_the_hosted_app"]
        given = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}

        assert given["sibling_checkouts"] == "manifest.runtime.sibling_checkouts"
        assert given["in_a_headset"] == "True"

    def test_a_session_with_no_checkout_named_still_hosts_none(self, config, tmp_path):
        path = write_manifest_data(build_vr_manifest(config), tmp_path / "launch.ini")
        bridge = build_bridge_config_from_manifest(
            LaunchManifest.read(path), vr_main_funestra=True)

        assert bridge.origenerator_enabled is False

    def test_a_room_asks_for_both_satellites_slideshows_as_it_opens_whatever_it_was_left_in(
            self, hosted, tmp_path):
        state_dir = hosted.paths.state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        write_shared_state(shared_state_path(state_dir), BridgeState())
        manifest = LaunchManifest.read(
            write_manifest_data(build_vr_manifest(hosted), tmp_path / "launch.ini"))
        Path(manifest.commands.origenerator_status_file).write_text(
            "paused = 0\n", encoding="utf-8")
        verbs: list[str] = []

        with patch("fun_time.runtime_flow.append_command",
                   side_effect=an_app_that_opens_each_slideshow_asked_of_it(
                       build_bridge_config_from_manifest(manifest, vr_main_funestra=True),
                       verbs)):
            _end_a_vr_session(
                orchestrator, hosted, ended_by=lambda *_a, **_k: "asked",
                bring_up_the_hosted_app=MagicMock(return_value=None),
                resume_playlists=MagicMock(return_value=True),
                DispatchLoopRunner=orchestrator.DispatchLoopRunner,
            )

        assert {"portrait_open_slideshow", "landscape_open_slideshow"} <= set(verbs), (
            "the headset's room opened in kino mode")

    def test_a_room_that_took_over_an_app_showing_a_slideshow_asks_for_the_slideshows_as_it_opens(
            self, hosted, tmp_path):
        hosted.paths.state_dir.mkdir(parents=True, exist_ok=True)
        manifest = LaunchManifest.read(
            write_manifest_data(build_vr_manifest(hosted), tmp_path / "launch.ini"))
        Path(manifest.commands.origenerator_status_file).write_text(
            "paused = 0\n", encoding="utf-8")
        verbs: list[str] = []

        with patch("fun_time.runtime_flow.append_command",
                   side_effect=an_app_that_opens_each_slideshow_asked_of_it(
                       build_bridge_config_from_manifest(manifest, vr_main_funestra=True),
                       verbs)):
            _end_a_vr_session(
                orchestrator, hosted, ended_by=lambda *_a, **_k: "asked",
                bring_up_the_hosted_app=MagicMock(return_value=HostedApp(
                    303, already_open=True, taken_over=True,
                    showing_a_slideshow=True)),
                resume_playlists=MagicMock(return_value=True),
                DispatchLoopRunner=orchestrator.DispatchLoopRunner,
            )

        assert {"portrait_open_slideshow", "landscape_open_slideshow"} <= set(verbs), (
            "the headset's room opened in kino mode")

    def test_the_wait_is_named_on_the_loading_screen_before_the_line_that_fills_the_bar(self):
        calls = _call_lines_in_run_vr_bridge()

        assert (calls["finish_under_the_loading_screen"]
                < calls["progress.advance"])

    def test_the_mode_is_taken_up_before_done_brings_the_loading_screen_down(self):
        calls = _call_lines_in_run_vr_bridge()

        assert (calls["finish_under_the_loading_screen"]
                < calls["progress.finish"])


class _FakeProc:
    def __init__(self, exits_after_polls=None):
        self._exits_after = exits_after_polls
        self._polls = 0
        self.terminated = False

    def poll(self):
        if self._exits_after is None:
            return None
        self._polls += 1
        return 0 if self._polls > self._exits_after else None

    def terminate(self):
        self.terminated = True
        self._exits_after = 0

    def wait(self):
        return 0


class TestWaitForSessionEnd:
    def test_ahk_exit_ends_the_session(self, tmp_path):
        assert _wait_for_session_end(
            _FakeProc(exits_after_polls=2), _FakeProc(), state_dir=tmp_path, poll_s=0.0
        ) == "ahk"

    def test_a_marked_end_leaves_the_script_up_to_hear_esc_on_the_closing_screen(self, tmp_path):
        (tmp_path / SESSION_END_MARKER).write_text("the quit chord", encoding="utf-8")

        assert _wait_for_session_end(
            _FakeProc(), _FakeProc(), state_dir=tmp_path, poll_s=0.0
        ) == "asked"

    def test_no_hotkey_script_outlives_the_teardown_still_holding_the_keys_it_binds(self):
        tree = ast.parse(_the_session_as_one_source())
        (session,) = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and any(
            isinstance(c, ast.Call) and ast.unparse(c.func) == "_wait_for_session_end"
            for c in ast.walk(n))]
        calls = [ast.unparse(c.func) for stmt in session.finalbody for c in ast.walk(stmt)
                 if isinstance(c, ast.Call)]

        assert "stop_hotkey_script" in calls
        assert calls.index("kill_recorded_child") < calls.index("stop_hotkey_script")

    def test_closing_the_headsets_only_window_ends_the_session(self, tmp_path):
        assert _wait_for_session_end(
            _FakeProc(), _FakeProc(exits_after_polls=2), state_dir=tmp_path, poll_s=0.0
        ) == "funestras"

    def test_any_other_part_of_the_room_closing_ends_the_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(funestra_deaths, "process_started", lambda pid: None)

        assert _wait_for_session_end(
            _FakeProc(), _FakeProc(), state_dir=tmp_path, poll_s=0.0,
            parts={"origenerator_pid": ChildProcess(pid=40, created_at=4000)},
        ) == "part"


class TestTheModeASessionComesBackIn:
    @staticmethod
    def _calls(function_name: str, spelling: str):
        source = (_the_session_as_one_source() if function_name == "run_vr_bridge"
                  else inspect.getsource(getattr(orchestrator, function_name)))
        tree = ast.parse(source)
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == spelling]

    def test_the_state_is_no_longer_corrected_on_the_way_in(self, tmp_path):
        assert not hasattr(orchestrator, "resume_vr_state")
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_mode=MainMode.GENAU, volume=40))

        assert read_shared_state(state_file).main_mode is MainMode.GENAU

    def test_the_flags_are_seeded_in_the_mode_the_dispatch_loop_will_read(self):
        (seed,) = self._calls("run_vr_bridge", "seed_startup_states")

        assert [ast.unparse(argument) for argument in seed.args] == ["manifest", "carried"]

    def test_genau_is_sent_the_shapes_it_was_left_browsing(self):
        (seed,) = self._calls("run_vr_bridge", "seed_startup_states")
        (resume,) = self._calls("run_vr_bridge", "resume_genau_shapes")

        assert resume.lineno > seed.lineno
        assert [ast.unparse(arg) for arg in resume.args] == [
            "Path(commands.genau_cmd_file)", "genau_flick_shapes(carried, bridge_config)"]

    def test_the_playlists_are_stocked_under_the_state_the_session_was_left_in(self):
        (stock,) = self._calls("run_vr_bridge", "stock_the_playlists")
        given = {kw.arg: ast.unparse(kw.value) for kw in stock.keywords}

        assert (given["carried"], given["main_video"]) == (
            "carried", "main_funestra_status.video")

    def test_the_flags_are_seeded_from_the_state_the_stocked_list_answers(self, config):
        _left_in(config, BridgeState(main_mode=MainMode.GENAU))
        the_list_answers_kino = MagicMock(
            side_effect=lambda _launch, **asked: replace(asked["carried"], main_mode=MainMode.KINO))

        queued = _queued_when_the_funestra_launches(
            config, SessionEnvironment(), stock_the_playlists=the_list_answers_kino)

        assert show_verb(MainMode.KINO) in queued["main"]
        assert show_verb(MainMode.GENAU) not in queued["main"]

    def test_the_loop_that_lived_only_in_the_desktops_funestra_is_queued_again(self):
        (resume,) = self._calls("run_vr_bridge", "resume_main_loop")

        assert [ast.unparse(arg) for arg in resume.args] == [
            "Path(commands.main_funestra_cmd_file)",
            "carried",
            "bridge_config.main_list.file",
        ]

    def test_the_loop_is_queued_once_the_stocked_list_says_which_video_opens(self):
        source = _the_session_as_one_source()

        assert source.index("stock_the_playlists(") < source.index("resume_main_loop(")

    def test_the_headset_comes_back_as_quiet_as_the_osr2_was_left(self):
        (seed,) = self._calls("run_vr_bridge", "seed_startup_states")

        assert [ast.unparse(argument) for argument in seed.args] == ["manifest", "carried"]

    def test_the_headset_is_seeded_with_everything_the_desktop_is(self):
        (headset,) = self._calls("run_vr_bridge", "seed_startup_states")
        desktop_source = ast.parse(inspect.getsource(windows_bridge_startup.start_core_session))
        (desktop,) = [n for n in ast.walk(desktop_source) if isinstance(n, ast.Call)
                      and ast.unparse(n.func) == "seed_startup_states"]

        assert ({kw.arg for kw in headset.keywords}, len(headset.args)) == (
            {kw.arg for kw in desktop.keywords}, len(desktop.args))

    def test_the_reveal_releases_the_funestras_the_mode_puts_to_work(self):
        (release,) = self._calls("run_vr_bridge", "release_the_funestras")

        assert [ast.unparse(arg) for arg in release.args] == ["manifest", "carried.main_mode"]
        assert self._calls("run_vr_bridge", "write_flag_file") == []


class TestLaunchVrFunestras:
    def test_the_funestra_with_no_console_leaves_its_output_in_a_log(self, tmp_path):
        with patch("fun_time_vr.orchestrator.subprocess.Popen") as popen:
            launch_vr_funestras(
                python_exe=tmp_path / "python.exe",
                manifest_path=tmp_path / "windows_bridge_launch.ini",
                log_file=tmp_path / "vr_funestras.log",
            )

        command = popen.call_args[0][0]
        assert command == [
            str(tmp_path / "python.exe"), "-m", VR_FUNESTRAS_MODULE,
            "--manifest", str(tmp_path / "windows_bridge_launch.ini"),
        ]
        kept = popen.call_args.kwargs
        assert kept["stdout"] is kept["stderr"]
        assert Path(kept["stdout"].name) == tmp_path / "vr_funestras.log"
        assert kept["creationflags"] & subprocess.CREATE_NO_WINDOW


class TestWaitForFunestra:
    class _Alive:
        returncode = None

        def poll(self):
            return None

    class _Dead:
        returncode = 3

        def poll(self):
            return 3

    def test_the_first_status_write_is_ready(self, tmp_path):
        status = tmp_path / "main_player_status.txt"
        status.write_text("video=C:\\v\\scene one.mp4\n", encoding="utf-8")

        assert _wait_for_funestra(status, self._Alive()) is True

    def test_an_early_death_is_reported_at_once_not_after_the_timeout(self, tmp_path, caplog):
        with caplog.at_level(logging.ERROR, logger="fun_time_vr.orchestrator"):
            ready = _wait_for_funestra(tmp_path / "main_player_status.txt", self._Dead())

        assert ready is False
        assert "exited during startup" in caplog.text
        assert "3" in caplog.text

    def test_a_silent_funestra_is_given_up_on_at_the_deadline(self, tmp_path, caplog, monkeypatch):
        monkeypatch.setattr(orchestrator, "FUNESTRA_READY_TIMEOUT_S", 0.0)
        with caplog.at_level(logging.ERROR, logger="fun_time_vr.orchestrator"):
            ready = orchestrator._wait_for_funestra(tmp_path / "main_player_status.txt", self._Alive())

        assert ready is False
        assert "published no status" in caplog.text

    def test_the_first_status_is_waited_past_a_cold_runtime_start_and_the_bring_up(self):
        assert orchestrator.FUNESTRA_READY_TIMEOUT_S > (
            vr_runtime.STARTUP_TIMEOUT_S + SESSION_BRINGUP_TIMEOUT_S)


class TestTheCheckRun:
    def test_a_valid_config_checks_out(self, config, monkeypatch, tmp_path):
        with patch.object(orchestrator, "load_config", return_value=config), \
             mutex_free(), \
             patch.object(orchestrator, "install_exception_logging"):
            assert orchestrator.main(["--check"]) == 0

    def test_the_session_s_log_holds_its_own_lines_and_the_vr_runtime_s(self, config):
        assert orchestrator.logger.name == "fun_time_vr.orchestrator"
        with patch.object(orchestrator, "load_config", return_value=config), \
             mutex_free(), \
             patch.object(orchestrator, "install_exception_logging"):
            orchestrator.main(["--check"])
        orchestrator.logger.info("a line of the session's own")
        logging.getLogger(vr_runtime.__name__).info("a line about the VR runtime")

        session_log = config.log_file("vr_orchestrator").read_text(encoding="utf-8")
        assert "a line of the session's own" in session_log
        assert "a line about the VR runtime" in session_log


@dataclasses.dataclass
class _AConfig:
    regen_metadata_root: Path


@dataclasses.dataclass
class _AStockingLaunch:
    manifest: LaunchManifest
    state_dir: Path
    bridge_config: _AConfig


@dataclasses.dataclass
class _TheCommands:
    origenerator_cmd_file: str


@dataclasses.dataclass
class _AHostedApp:
    taken_over: bool


@dataclasses.dataclass
class _ACancelledLaunch:
    state_dir: Path
    ahk_cmd_file: Path
    loading_screen: orchestrator._LoadingScreen
    commands: _TheCommands

    @property
    def progress(self):
        return self.loading_screen.progress


class TestStockingThePlaylistsThroughFunTimesOwnBuilders:
    def _stock(self, config, tmp_path, *, resumed: bool, carried: BridgeState | None = None,
               main_video: str = "") -> BridgeState:
        manifest = LaunchManifest.read(
            write_manifest_data(build_vr_manifest(config), tmp_path / "launch.ini"))
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        launch = _AStockingLaunch(
            manifest, state, _AConfig(regen_metadata_root=tmp_path / "metadata"))
        return stock_the_playlists(
            launch, resumed=resumed, carried=carried or BridgeState(), main_video=main_video)

    @staticmethod
    def _main_list(tmp_path) -> list[str]:
        return [line.split("\t")[0] for line in (
            tmp_path / "state" / "main_player_playlist.tsv").read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def _library(tmp_path) -> tuple[Path, Path]:
        library = tmp_path / "library"
        vr_video, flat = library / "VR" / "finished" / "scene one.mp4", library / "2D" / "scene two.mp4"
        for video in (vr_video, flat):
            video.write_bytes(b"")
        return vr_video, flat

    def test_a_fresh_session_gets_all_three(self, config, tmp_path):
        self._library(tmp_path)

        self._stock(config, tmp_path, resumed=False)

        written = sorted(p.name for p in (tmp_path / "state").glob("*playlist*.tsv"))
        assert len(written) == 3, written

    def test_the_main_list_comes_up_on_the_video_it_was_on_and_the_headsets_library_under_it(
        self, config, tmp_path,
    ):
        vr_video, flat = self._library(tmp_path)
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "main_player_playlist.tsv").write_text(f"{flat}\n", encoding="utf-8")

        self._stock(config, tmp_path, resumed=True, main_video=str(flat))

        assert self._main_list(tmp_path) == [str(flat), str(vr_video)]

    def test_a_video_gone_since_opens_the_main_list_at_its_top(self, config, tmp_path):
        self._library(tmp_path)

        self._stock(config, tmp_path, resumed=True,
                    main_video=str(tmp_path / "library" / "2D" / "gone since.mp4"))

        assert "gone since" not in self._main_list(tmp_path)[0]

    def test_vr_only_holds_no_2d_video_whatever_the_last_list_held(self, config, tmp_path):
        vr_video, flat = self._library(tmp_path)
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "main_player_playlist.tsv").write_text(
            f"{flat}\n{vr_video}\n", encoding="utf-8")

        self._stock(config, tmp_path, resumed=True, main_video=str(flat),
                    carried=BridgeState(main_plays_flat=False))

        assert self._main_list(tmp_path) == [str(vr_video)]

    def test_2d_only_holds_every_2d_video_and_no_vr_one(self, config, tmp_path):
        _vr_video, flat = self._library(tmp_path)
        arrived = tmp_path / "library" / "2D" / "scene three.mp4"
        arrived.write_bytes(b"")

        self._stock(config, tmp_path, resumed=True, main_video=str(flat),
                    carried=BridgeState(main_plays_vr=False))

        assert sorted(self._main_list(tmp_path)) == sorted([str(flat), str(arrived)])

    def test_a_session_left_playing_neither_shape_reopens_on_the_video_it_was_left_on(
        self, config, tmp_path,
    ):
        vr_video, _flat = self._library(tmp_path)

        self._stock(config, tmp_path, resumed=True, main_video=str(vr_video),
                    carried=BridgeState(main_plays_vr=False, main_plays_flat=False))

        assert self._main_list(tmp_path) == [str(vr_video)]


class TestResumeGenausShapes:
    def _shapes(self, tmp_path: Path, *, plays_vr: bool, plays_flat: bool) -> VideoShapes:
        return VideoShapes(vr_dirs=str(tmp_path / "vr_flicks"),
                           plays_vr=plays_vr, plays_flat=plays_flat)

    def test_a_genau_that_rescans_whole_at_launch_is_sent_its_one_shape_again(self, tmp_path: Path):
        genau = tmp_path / "genau_cmd.txt"
        genau.write_text("PAUSE\n", encoding="utf-8")

        resume_genau_shapes(genau, self._shapes(tmp_path, plays_vr=False, plays_flat=True))

        assert genau.read_text(encoding="utf-8").splitlines() == ["PAUSE", "SHAPES flat"]

    def test_queues_nothing_while_genau_plays_both(self, tmp_path: Path):
        genau = tmp_path / "genau_cmd.txt"

        resume_genau_shapes(genau, self._shapes(tmp_path, plays_vr=True, plays_flat=True))

        assert not genau.exists()

    def test_queues_nothing_for_neither_since_genau_launches_holding_its_flick(self, tmp_path: Path):
        genau = tmp_path / "genau_cmd.txt"

        resume_genau_shapes(genau, self._shapes(tmp_path, plays_vr=False, plays_flat=False))

        assert not genau.exists()

    def test_queues_nothing_to_a_genau_with_only_flat_flicks(self, tmp_path: Path):
        genau = tmp_path / "genau_cmd.txt"

        resume_genau_shapes(genau, VideoShapes(vr_dirs="", plays_vr=True, plays_flat=False))

        assert not genau.exists()


class TestTheCrossingBackToTheDesktop:
    def _main(self, config, *, during_session=lambda: None):
        with patch.object(orchestrator, "load_config", return_value=config), \
             patch.object(orchestrator, "configure_logging", return_value=MagicMock()), \
             patch.object(orchestrator, "install_exception_logging"), \
             mutex_free(), \
             patch("fun_time.session_handoff.subprocess.Popen") as popen, \
             patch.object(orchestrator, "engine_missing_abort", return_value=False), \
             patch.object(orchestrator, "run_vr_bridge",
                          side_effect=lambda *_a, **_k: (during_session(), 0)[1]):
            return orchestrator.main([]), popen

    def test_a_session_that_asked_to_cross_spawns_the_relay_on_its_way_out(self, config):
        code, popen = self._main(
            config,
            during_session=lambda: request_handoff(config.paths.state_dir, DESKTOP),
        )

        assert code == 0
        assert popen.call_args.args[0][3:6] == ["--target", "desktop", "--config"]

    def test_an_ordinary_quit_spawns_nothing(self, config):
        _code, popen = self._main(config)

        popen.assert_not_called()

    def test_a_request_a_crash_left_behind_never_reaches_the_next_session(self, config):
        request_handoff(config.paths.state_dir, DESKTOP)

        _code, popen = self._main(config)

        popen.assert_not_called()


def _the_session_main_starts(config, argv: list[str]) -> MagicMock:
    with patch.object(orchestrator, "load_config", return_value=config), \
         patch.object(orchestrator, "configure_logging", return_value=MagicMock()), \
         patch.object(orchestrator, "install_exception_logging"), \
         mutex_free(), \
         patch("fun_time.session_handoff.subprocess.Popen"), \
         patch.object(orchestrator, "engine_missing_abort", return_value=False), \
         patch.object(orchestrator, "run_vr_bridge", return_value=0) as run_bridge:
        orchestrator.main(argv)
    return run_bridge


def test_a_vr_session_and_a_desktop_session_claim_one_mutex(config):
    claimed: dict[str, str] = {}

    def main_of(module) -> None:
        with patch.object(module, "configure_logging", return_value=MagicMock()), \
             patch.object(module, "install_exception_logging"), \
             patch.object(module, "show_already_running_message"), \
             patch.object(module, "claim_the_session",
                          side_effect=lambda name: claimed.__setitem__(module.__name__, name)):
            assert module.main(["--config", str(config.config_path)]) == 1

    main_of(desktop_orchestrator)
    main_of(orchestrator)

    assert claimed["fun_time_vr.orchestrator"] == claimed["fun_time.orchestrator"] != ""


class TestTheCheckoutsItsOwnCheckoutNames:
    def test_the_session_runs_the_sibling_checkouts_this_checkout_names(
            self, config, tmp_path, monkeypatch):
        checkout = tmp_path / "funestra_worktree"
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / SIBLING_CHECKOUTS_OVERRIDE_NAME).write_text(
            f"{checkout}\n", encoding="utf-8")
        monkeypatch.setattr(checkout_overrides, "PROJECT_DIR", tmp_path)

        run_bridge = _the_session_main_starts(config, [])

        assert run_bridge.call_args.args[0].paths.sibling_checkouts == (checkout,)


class TestTheWayBackIntoVr:
    def test_a_launch_started_as_a_way_back_is_handed_no_esc(self, config):
        run_bridge = _the_session_main_starts(config, ["--no-cancel"])

        assert run_bridge.call_args.kwargs["cancelable"] is False

    def test_a_funestra_left_holding_the_headset_is_gone_before_the_new_one_starts(
        self, config,
    ):
        class FunestraLaunched(Exception):
            pass

        state_dir = config.paths.state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        hold_the_headset(state_dir, stop_runtime=True)
        held_while_reaping: list[bool] = []
        held_at_launch: list[bool] = []

        def reap(module, _claimed):
            if module == orchestrator.VR_FUNESTRAS_MODULE:
                held_while_reaping.append(headset_hold_asked(state_dir))

        def launch(**_kwargs):
            held_at_launch.append(headset_hold_asked(state_dir))
            raise FunestraLaunched

        with _launch_stand_ins(
            orchestrator, [],
            reap_orphaned_satellites=MagicMock(side_effect=reap),
            launch_vr_funestras=MagicMock(side_effect=launch),
        ), patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=True), \
             patch("fun_time_vr.orchestrator.subprocess.Popen"), \
             pytest.raises(FunestraLaunched):
            orchestrator.run_vr_bridge(config, SessionEnvironment())

        assert held_while_reaping == [True]
        assert held_at_launch == [False]


def _queued_when_the_funestra_launches(
    config, env: SessionEnvironment, **overrides,
) -> dict[str, list[str]]:
    class FunestraLaunched(Exception):
        pass

    channels = {
        "main": config.main_funestra_cmd_file,
        "portrait": config.satellite(FunTimeFunestra.PORTRAIT).cmd_file,
        "landscape": config.satellite(FunTimeFunestra.LANDSCAPE).cmd_file,
        "genau": config.genau_cmd_file,
    }
    queued: dict[str, list[str]] = {}

    def launch(**_kwargs):
        for name, path in channels.items():
            queued[name] = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        raise FunestraLaunched

    with _launch_stand_ins(orchestrator, [], launch_vr_funestras=MagicMock(side_effect=launch),
                           **overrides), \
         patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=True), \
         patch("fun_time_vr.orchestrator.subprocess.Popen"), \
         pytest.raises(FunestraLaunched):
        orchestrator.run_vr_bridge(config, env)
    return queued


def _left_in(config, state: BridgeState) -> None:
    config.paths.state_dir.mkdir(parents=True, exist_ok=True)
    write_shared_state(shared_state_path(config.paths.state_dir), state)


class TestWhatAReopenCarriesIntoTheHeadset:
    def test_each_funestra_is_handed_the_rate_it_was_playing_at(self, config):
        _left_in(config, BridgeState(main_speed=0.25, portrait=SatelliteState(speed=0.5)))

        queued = _queued_when_the_funestra_launches(config, SessionEnvironment())

        assert "SET_SPEED 0.25" in queued["main"]
        assert "SET_SPEED 0.5" in queued["portrait"]
        assert not any(verb.startswith("SET_SPEED") for verb in queued["landscape"])

    def test_the_main_funestra_is_handed_back_the_unlock_it_was_left_in(self, config):
        _left_in(config, BridgeState(main_locked=False))

        queued = _queued_when_the_funestra_launches(config, SessionEnvironment())

        assert "LOCK_OFF" in queued["main"]

    def test_genau_is_handed_back_the_robot_hand_it_was_left_with(self, config):
        _left_in(config, BridgeState(genau_speed=76, genau_learned=True))

        queued = _queued_when_the_funestra_launches(config, SessionEnvironment())

        assert queued["genau"][0] == "PAUSE", "the fresh session's reset must still lead"
        assert queued["genau"][-2:] == ["SPEED 76", "LEARNED_ON"]

    def test_genaus_shapes_outlast_the_seeding_that_writes_its_command_file_whole(self, config):
        _left_in(config, BridgeState(genau_plays_vr=False))

        queued = _queued_when_the_funestra_launches(config, SessionEnvironment())

        assert queued["genau"][0] == "PAUSE"
        assert "SHAPES flat" in queued["genau"]

class TestHandingTheHeadsetOver:
    class _Alive:
        def poll(self):
            return None

    class _Dead:
        def poll(self):
            return 1

    def test_a_funestra_that_takes_the_hold_is_left_running(self, tmp_path: Path):
        with patch("fun_time_vr.orchestrator.headset_is_held", return_value=True):
            assert _wait_for_the_headset_hold(tmp_path, self._Alive()) is True

    def test_a_hold_never_taken_is_given_up_for_the_session_to_come(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=False)

        with patch.object(orchestrator, "HEADSET_CLOSES_ITS_CHANNELS_TIMEOUT_S", 0.0):
            assert orchestrator._wait_for_the_headset_hold(tmp_path, self._Alive()) is False

        assert headset_hold_asked(tmp_path) is False, "the next launch must not read it"

    def test_a_funestra_that_has_already_gone_is_not_waited_out(self, tmp_path: Path, caplog):
        hold_the_headset(tmp_path, stop_runtime=False)
        started = time.monotonic()

        with caplog.at_level(logging.WARNING, logger="fun_time_vr.orchestrator"):
            answered = orchestrator._wait_for_the_headset_hold(tmp_path, self._Dead())

        assert answered is False
        assert time.monotonic() - started < 1.0
        assert "exited without taking the headset hold" in caplog.text
        assert headset_hold_asked(tmp_path) is False, "the next launch must not read it"


def test_a_session_stops_the_runtime_it_started_with_no_window_to_quit(monkeypatch, tmp_path):
    stopped = []
    monkeypatch.setattr(
        "fun_time_vr.orchestrator.vr_runtime.stop_the_runtime_a_session_started", stopped.append)
    _release_vr_runtime(was_up=False, state_dir=tmp_path)
    assert stopped == [tmp_path]


def test_a_session_leaves_a_vr_runtime_that_was_already_the_users(monkeypatch, tmp_path):
    stopped = []
    monkeypatch.setattr(
        "fun_time_vr.orchestrator.vr_runtime.stop_the_runtime_a_session_started", stopped.append)
    _release_vr_runtime(was_up=True, state_dir=tmp_path)
    assert stopped == []


def test_a_runtime_fun_time_woke_is_put_to_sleep_when_vr_is_the_last_room(
        monkeypatch, tmp_path):
    stopped = []
    monkeypatch.setattr(
        "fun_time_vr.vr_runtime.stop_the_runtime_a_session_started", stopped.append)
    (tmp_path / OUR_RUNTIME_NAME).write_text("", encoding="utf-8")

    _release_vr_runtime(was_up=True, state_dir=tmp_path)

    assert stopped == [tmp_path]


def test_an_error_that_ends_fun_time_vr_puts_the_runtime_fun_time_woke_to_sleep(
        config, monkeypatch):
    stopped = []
    monkeypatch.setattr(
        "fun_time_vr.vr_runtime.stop_the_runtime_a_session_started", stopped.append)
    state_dir = config.paths.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / OUR_RUNTIME_NAME).write_text("", encoding="utf-8")

    with patch.object(orchestrator, "load_config", return_value=config), \
         patch.object(orchestrator, "configure_logging", return_value=MagicMock()), \
         patch.object(orchestrator, "install_exception_logging"), \
         mutex_free(), \
         patch.object(orchestrator, "engine_missing_abort", return_value=False), \
         patch.object(orchestrator, "run_vr_bridge",
                      side_effect=RuntimeError("an error of its own")), \
         pytest.raises(RuntimeError, match="an error of its own"):
        orchestrator.main([])

    assert stopped == [state_dir]


class TestGenausRoleInTheManifest:
    def test_genaus_flicks_folder_rides_whole(self, config):
        vr = build_vr_manifest(config)["vr"]

        assert vr["flicks_folder"] == str(config.paths.flicks_dir)

    def test_the_dispatch_loop_is_told_which_folder_holds_the_vr_flicks(self, config, tmp_path):
        def bridge_config(session_config):
            path = write_manifest_data(build_vr_manifest(session_config), tmp_path / "launch.ini")
            return build_bridge_config_from_manifest(LaunchManifest.read(path), vr_main_funestra=True)

        assert bridge_config(config).genau_vr_flicks == str(config.paths.flicks_dir / "VR")

    def test_the_companions_address_is_fun_times_own(self, config):
        vr = build_vr_manifest(config)["vr"]

        assert (vr["notify_host"], vr["notify_port"]) == ("127.0.0.1", "50556")

    def test_genaus_numbers_come_off_the_genau_section_of_fun_times_config(self, config):
        raw = json.loads(config.config_path.read_text(encoding="utf-8"))
        raw["genau"] = {"beats_per_loop": 2.0, "udp_port": 50999}
        config.config_path.write_text(json.dumps(raw), encoding="utf-8")

        vr = build_vr_manifest(load_config(config.config_path))["vr"]

        assert vr["beats_per_loop"] == "2.0"
        assert vr["beat_udp_port"] == "50999"
        assert vr["bpm_smoothing"] == str(GenauSettings().bpm_smoothing)

    def test_the_funestra_reads_all_of_it_back(self, config, tmp_path):
        path = write_manifest_data(build_vr_manifest(config), tmp_path / "manifest.ini")

        settings = VrSettings.read(path)

        assert settings.flicks_folder == config.paths.flicks_dir
        assert (settings.notify_host, settings.notify_port) == ("127.0.0.1", 50556)
        assert settings.genau == GenauSettings()


class TestTheOnePin:
    def test_a_vr_session_claims_fun_times_own_identity(self):
        source = inspect.getsource(app.main)
        claimed = [
            ast.unparse(node.args[0])
            for node in ast.walk(ast.parse(source.lstrip()))
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "set_app_user_model_id"
        ]
        assert claimed == ["preview_marker.session_identity(preview_marker.shown_as())"]

    def test_the_identity_is_claimed_before_any_window_of_the_session_exists(self):
        called_at = {ast.unparse(node.func): node.lineno
                     for node in ast.walk(ast.parse(inspect.getsource(app.main)))
                     if isinstance(node, ast.Call)}
        what_opens_a_window = ("_say_why_the_headset_never_lit_up", "_run")

        assert all(called_at["set_app_user_model_id"] < called_at[opener]
                   for opener in what_opens_a_window)

    def test_there_is_no_second_identity_left_to_claim(self):
        assert not [name for name in vars(win32_taskbar) if name.endswith("APP_USER_MODEL_ID")
                    and name != "APP_USER_MODEL_ID"]

    def test_a_session_describes_the_button_of_the_checkout_it_runs_from(self, config):
        with patch.object(orchestrator, "load_config", return_value=config), \
             mutex_free(), \
             patch.object(orchestrator, "install_exception_logging"), \
             patch.object(orchestrator, "describe_the_session_on_the_taskbar") as describe:
            orchestrator.main(["--check"])

        describe.assert_called_once_with(PROJECT_DIR)

    def test_a_session_on_another_config_leaves_the_pin_alone(self, config):
        with patch.object(orchestrator, "load_config", return_value=config), \
             mutex_free(), \
             patch.object(orchestrator, "install_exception_logging"), \
             patch.object(orchestrator, "stamp_the_users_pin_outside_every_checkout") as stamp:
            orchestrator.main(["--check"])

        stamp.assert_not_called()

    def test_the_installed_app_stamps_the_one_pin(self, config):
        with patch.object(orchestrator, "load_config", return_value=config), \
             patch.object(orchestrator, "DEFAULT_CONFIG_PATH", config.config_path), \
             mutex_free(), \
             patch.object(orchestrator, "install_exception_logging"), \
             patch.object(orchestrator, "stamp_the_users_pin_outside_every_checkout") as stamp:
            orchestrator.main(["--check"])

        stamp.assert_called_once_with()


def _the_session_as_one_source() -> str:
    steps = {name: thing for name, thing in vars(orchestrator).items()
             if inspect.isfunction(thing) and name.startswith("_")}

    def written_out(function, seen: frozenset) -> list[str]:
        source = textwrap.dedent(inspect.getsource(function))
        called_at: dict[int, list[tuple[int, str]]] = {}
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.stmt):
                continue
            own = [child for name, child in ast.iter_fields(node)
                   if not (isinstance(child, list) and child and isinstance(child[0], ast.stmt))]
            for piece in own:
                for call in ast.walk(piece) if isinstance(piece, ast.AST) else ():
                    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id in steps and call.func.id not in seen):
                        called_at.setdefault(node.lineno, []).append((node.col_offset, call.func.id))
        lines: list[str] = []
        for number, line in enumerate(source.splitlines(), start=1):
            for indent, name in called_at.get(number, []):
                lines += [" " * indent + inner if inner else inner
                          for inner in written_out(steps[name], seen | {name})]
            lines.append(line)
        return lines

    return "\n".join(written_out(orchestrator.run_vr_bridge, frozenset()))


def _call_lines_in_run_vr_bridge() -> dict[str, int]:
    calls = sorted((n for n in ast.walk(ast.parse(_the_session_as_one_source()))
                    if isinstance(n, ast.Call)), key=lambda n: n.lineno)
    return {ast.unparse(n.func): n.lineno for n in calls}


class TestTheAudioCompanionInVr:
    def test_it_is_launched_on_the_headsets_output(self):
        tree = ast.parse(_the_session_as_one_source())
        (launch,) = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and ast.unparse(n.func) == "launch_audio_companion"]
        given = {kw.arg: ast.unparse(kw.value) for kw in launch.keywords}

        assert [ast.unparse(argument) for argument in launch.args] == ["manifest"]
        assert given["audio_device"] == "config.vr.audio_device"

    def test_no_dashboard_is_launched_beside_it_to_show_a_message(self):
        launched = {ast.unparse(n.func) for n in ast.walk(ast.parse(_the_session_as_one_source()))
                    if isinstance(n, ast.Call)}

        assert "launch_audio_companion" in launched
        assert not {name for name in launched if "dashboard" in name or "ui_companions" in name}

    def test_it_is_listening_before_genaus_role_can_name_its_first_flick(self):
        calls = _call_lines_in_run_vr_bridge()

        assert calls["launch_audio_companion"] < calls["launch_vr_funestras"]

    def test_it_is_killed_with_the_funestra(self):
        assert "for child in launched.children.values():" in _the_session_as_one_source()


def _every_done_said(monkeypatch) -> list[str]:
    said: list[str] = []

    class Recording(PhaseProgress):
        def finish(self) -> None:
            said.append("DONE")
            super().finish()

    monkeypatch.setattr(orchestrator, "PhaseProgress", Recording)
    return said


class TestTheHeadsetsLoadingScreen:
    def test_a_stale_cancel_flag_cannot_abort_the_next_launch(self, tmp_path):
        stale = tmp_path / CANCEL_FILENAME
        stale.write_text("cancel\n", encoding="utf-8")

        loading_screen = _LoadingScreen(tmp_path)

        assert not stale.exists()
        assert not loading_screen.progress.cancelled

    def test_a_launch_says_esc_cancels_opening_fun_time_vr(self, tmp_path):
        loading_screen = _LoadingScreen(tmp_path)
        loading_screen.progress.advance("services")

        assert parse_progress(loading_screen.progress_file.read_text(encoding="utf-8")).hint == (
            "Press Esc to cancel opening Fun Time VR")

    def test_a_session_entered_from_fun_time_says_esc_cancels_entering_vr(self, tmp_path):
        raise_transition_screen(tmp_path, VR)
        loading_screen = _LoadingScreen(tmp_path)
        loading_screen.progress.advance("services")

        assert parse_progress(loading_screen.progress_file.read_text(encoding="utf-8")).hint == (
            "Press Esc to cancel entering VR")

    def test_an_esc_pressed_while_the_room_changed_over_calls_the_arrival_off(self, tmp_path):
        raise_transition_screen(tmp_path, VR)
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        assert _LoadingScreen(tmp_path).progress.cancelled

    def test_a_session_entered_on_the_way_back_offers_no_esc(self, tmp_path):
        raise_transition_screen(tmp_path, VR)
        loading_screen = _LoadingScreen(tmp_path, cancelable=False)
        loading_screen.progress.advance("services")

        assert parse_progress(loading_screen.progress_file.read_text(encoding="utf-8")).hint == ""

    def test_clearing_leaves_the_loading_screen_to_go_with_the_funestra(
            self, tmp_path, monkeypatch):
        said = _every_done_said(monkeypatch)
        loading_screen = _LoadingScreen(tmp_path)
        loading_screen.progress.advance("funestras")

        loading_screen.clear()

        assert said == []
        assert not loading_screen.progress_file.exists()
        assert not loading_screen.cancel_file.exists()

    def test_the_script_that_hears_esc_is_up_before_anything_esc_could_cancel(self):
        calls = _call_lines_in_run_vr_bridge()

        assert calls["launch_hotkey_script"] < calls["launch_vr_funestras"]
        assert calls["launch_vr_funestras"] < calls["write_pids_file"]

    def test_no_video_plays_its_first_seconds_under_the_loading_screen(self):
        calls = _call_lines_in_run_vr_bridge()

        assert calls["progress.finish"] < calls["release_the_funestras"]


@dataclass(frozen=True)
class Cancelling:
    children: dict = field(default_factory=dict)
    by_quit_chord: bool = False
    crossing: bool = True
    while_ending: Callable = lambda child: None


class TestCancellingALaunch:
    def _cancel(self, tmp_path, monkeypatch, cancelling=None):
        cancelling = cancelling or Cancelling()
        children, while_ending = cancelling.children, cancelling.while_ending
        order: list = []

        def end(child):
            order.append(child.pid)
            while_ending(child)

        monkeypatch.setattr(orchestrator, "stop_hotkey_script",
                            lambda _proc, _file: order.append("hotkeys"))
        monkeypatch.setattr(orchestrator, "kill_recorded_child", end)
        if cancelling.crossing:
            raise_transition_screen(tmp_path, orchestrator.VR)
        loading_screen = orchestrator._LoadingScreen(tmp_path)
        loading_screen.progress.advance("funestras")
        loading_screen.cancel_file.write_text(
            "quit\n" if cancelling.by_quit_chord else "cancel\n", encoding="utf-8")
        (tmp_path / SESSION_END_MARKER).write_text("the quit chord\n", encoding="utf-8")

        launch = _ACancelledLaunch(
            tmp_path, tmp_path / "ahk_cmd.txt", loading_screen,
            _TheCommands(origenerator_cmd_file=str(tmp_path / "origenerator_cmd.txt")))
        launched = orchestrator._Launched(children=children, hosted=_AHostedApp(False))
        code = orchestrator._cancel_vr_startup(launch, launched, None)
        return code, order, loading_screen

    def test_esc_on_a_launch_nobody_crossed_into_closes_it(self, tmp_path, monkeypatch):
        self._cancel(tmp_path, monkeypatch, Cancelling(crossing=False))

        assert pending_handoff(tmp_path) is None

    def test_the_funestra_showing_canceling_is_killed_last_whatever_the_recorded_order(
            self, tmp_path, monkeypatch):
        children = {
            "vr_funestras_pid": ChildProcess(pid="funestras", created_at=1),
            "audio_pid": ChildProcess(pid="audio", created_at=1),
        }

        _code, order, _loading_screen = self._cancel(tmp_path, monkeypatch, Cancelling(children=children))

        assert order == ["hotkeys", "audio", "funestras"]

    def test_a_part_slow_to_end_does_not_let_the_headset_loading_screen_go(self, tmp_path, monkeypatch):
        heard: list[bool] = []

        def the_audio_companion_takes_its_time(child):
            if child.pid == "audio":
                heard.append(heard_from_again_once_stale(
                    tmp_path / PROGRESS_FILENAME, stale_after_s=STARTUP_STALE_TIMEOUT_S))

        self._cancel(tmp_path, monkeypatch, Cancelling(children={"audio_pid": ChildProcess(pid="audio", created_at=1)}, while_ending=the_audio_companion_takes_its_time))

        assert heard == [True]

    def test_it_leaves_no_files_behind_and_reads_as_a_clean_exit(
            self, tmp_path, monkeypatch):
        code, _order, loading_screen = self._cancel(tmp_path, monkeypatch)

        assert code == 0
        assert not loading_screen.progress_file.exists()
        assert not loading_screen.cancel_file.exists()

    def test_a_crossings_own_exit_marker_is_not_read_as_a_quit(
            self, tmp_path, monkeypatch):
        self._cancel(tmp_path, monkeypatch)

        assert pending_handoff(tmp_path) == DESKTOP

    def test_esc_never_leaves_the_transition_screen_up_with_no_session_behind_it(
            self, tmp_path, monkeypatch):
        self._cancel(tmp_path, monkeypatch)

        assert pending_handoff(tmp_path) == DESKTOP
        assert "DONE" not in crossing_progress_path(tmp_path).read_text(encoding="utf-8")

    def test_the_quit_chord_closes_the_hosted_app_fun_time_left_waiting(
            self, tmp_path, monkeypatch):
        closed: list = []
        monkeypatch.setattr(orchestrator, "let_go_of_a_kept_origenerator",
                            lambda state_dir, _cmd_file: closed.append(state_dir))

        self._cancel(tmp_path, monkeypatch, Cancelling(by_quit_chord=True))

        assert closed == [tmp_path]

    def test_esc_leaves_the_hosted_app_for_fun_time_to_adopt(self, tmp_path, monkeypatch):
        closed: list = []
        monkeypatch.setattr(orchestrator, "let_go_of_a_kept_origenerator",
                            lambda state_dir, _cmd_file: closed.append(state_dir))

        self._cancel(tmp_path, monkeypatch)

        assert closed == []

    def test_the_way_back_to_fun_time_offers_no_esc(self, tmp_path, monkeypatch):
        self._cancel(tmp_path, monkeypatch)

        taken = take_handoff_request(tmp_path)
        assert (taken.target, taken.cancelable) == (DESKTOP, False)

    def test_the_monitors_say_canceling_before_the_teardown_that_takes_seconds(
            self, tmp_path, monkeypatch):
        said_by_the_first_kill: list[str] = []

        self._cancel(tmp_path, monkeypatch, Cancelling(
            children={"audio_pid": ChildProcess(pid="audio", created_at=1)},
            while_ending=lambda _child: said_by_the_first_kill.append(
                crossing_progress_path(tmp_path).read_text(encoding="utf-8"))))

        assert len(said_by_the_first_kill) == 1
        assert CANCELING in said_by_the_first_kill[0]

    def test_the_quit_chord_takes_the_monitors_back_instead(
            self, tmp_path, monkeypatch):
        self._cancel(tmp_path, monkeypatch, Cancelling(by_quit_chord=True))

        assert pending_handoff(tmp_path) is None
        assert "DONE" in crossing_progress_path(tmp_path).read_text(encoding="utf-8")


class _AliveFunestra:
    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class TestTheClosingScreen:
    def test_teardown_holds_its_first_kill_until_the_loading_screen_is_painted(self, tmp_path):
        (tmp_path / SHUTDOWN_READY_FILENAME).write_text("", encoding="utf-8")
        seen = []

        with _closing_screen(tmp_path, _AliveFunestra(), enabled=True) as shutdown:
            seen.append(parse_progress(
                (tmp_path / SHUTDOWN_PROGRESS_FILENAME).read_text(encoding="utf-8")).message)
            shutdown.advance("funestras")

        assert seen == [CLOSING_STATUS]
        assert not (tmp_path / SHUTDOWN_PROGRESS_FILENAME).exists()
        assert not (tmp_path / SHUTDOWN_READY_FILENAME).exists()

    def test_it_says_what_esc_would_cancel(self, tmp_path):
        (tmp_path / SHUTDOWN_READY_FILENAME).write_text("", encoding="utf-8")

        with _closing_screen(tmp_path, _AliveFunestra(), enabled=True,
                            esc_cancels="Press Esc to cancel closing Fun Time VR"):
            line = parse_progress(
                (tmp_path / SHUTDOWN_PROGRESS_FILENAME).read_text(encoding="utf-8"))

        assert line.hint == "Press Esc to cancel closing Fun Time VR"

    def test_a_flag_from_a_previous_session_cannot_vouch_for_this_one(self, tmp_path):
        stale = tmp_path / SHUTDOWN_READY_FILENAME
        stale.write_text("", encoding="utf-8")
        cleared = []

        with _closing_screen(tmp_path, _AliveFunestra(alive=False), enabled=True):
            cleared.append(stale.exists())

        assert cleared == [False]

    def test_a_part_slow_to_end_does_not_let_the_headset_loading_screen_go(self, config):
        heard: list[bool] = []

        def the_audio_companion_takes_its_time(child):
            if child.pid == 101:
                heard.append(heard_from_again_once_stale(
                    config.paths.state_dir / SHUTDOWN_PROGRESS_FILENAME,
                    stale_after_s=SHUTDOWN_STALE_TIMEOUT_S))

        _end_a_vr_session(
            orchestrator, config, ended_by=lambda *_a, **_k: "asked",
            kill_recorded_child=MagicMock(side_effect=the_audio_companion_takes_its_time))

        assert heard == [True]

    def test_a_teardown_stuck_anywhere_but_ending_a_part_still_lets_the_headset_loading_screen_go(
            self, tmp_path):
        (tmp_path / SHUTDOWN_READY_FILENAME).write_text("", encoding="utf-8")
        heard: list[bool] = []

        with _closing_screen(tmp_path, _AliveFunestra(), enabled=True):
            heard.append(heard_from_again_once_stale(
                tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                stale_after_s=SHUTDOWN_STALE_TIMEOUT_S, within_s=2.5))

        assert heard == [False]

    def test_a_headset_cut_to_the_runtimes_environment_gets_no_closing_screen(self, tmp_path):
        with _closing_screen(tmp_path, _AliveFunestra(alive=False), enabled=False) as shutdown:
            shutdown.advance("funestras")

            assert not (tmp_path / SHUTDOWN_PROGRESS_FILENAME).exists()

    def test_no_closing_screen_when_the_funestra_that_would_draw_it_has_gone(self, config):
        died = MagicMock(pid=202)
        died.poll.return_value = 1
        offered_to: list[bool] = []

        @contextlib.contextmanager
        def closing_screen(*_args, enabled, **_kwargs):
            offered_to.append(enabled)
            yield NullProgress()

        _end_a_vr_session(orchestrator, config, ended_by=lambda *_a, **_k: "funestras",
                          launch_vr_funestras=MagicMock(return_value=died),
                          _closing_screen=closing_screen)

        assert offered_to == [False]

    def test_the_closing_screen_goes_with_the_funestra_not_on_a_done(self, tmp_path, monkeypatch):
        said = _every_done_said(monkeypatch)
        (tmp_path / SHUTDOWN_READY_FILENAME).write_text("", encoding="utf-8")

        with _closing_screen(tmp_path, _AliveFunestra(), enabled=True) as shutdown:
            shutdown.advance("funestras")

        assert said == []
        assert not (tmp_path / SHUTDOWN_PROGRESS_FILENAME).exists()

    def test_the_closing_line_is_there_before_the_funestra_is_asked_to_paint_it(
            self, tmp_path, monkeypatch):
        on_disk_when_asked: list[bool] = []
        monkeypatch.setattr(
            orchestrator, "wait_for_loading_screen_painted",
            lambda _ready_file, **_k: on_disk_when_asked.append(
                (tmp_path / SHUTDOWN_PROGRESS_FILENAME).exists()) or True)

        with _closing_screen(tmp_path, _AliveFunestra(), enabled=True):
            pass

        assert on_disk_when_asked == [True]

    def test_the_funestra_showing_the_closing_screen_is_the_last_child_killed(self):
        tree = ast.parse(_the_session_as_one_source())
        kills = [ast.unparse(n) for n in sorted(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "kill_recorded_child"),
            key=lambda n: n.lineno)]

        assert kills[-1] == "kill_recorded_child(launched.children['vr_funestras_pid'])"
        assert "kill_recorded_child(launched.children['audio_pid'])" in kills

    def test_everything_stops_and_the_osr2_is_held_before_either_loading_screen_goes_up(self):
        calls = _call_lines_in_run_vr_bridge()

        assert "stop_everything" in calls, "the teardown never stops the room"
        assert calls["stop_everything"] < calls["launch_transition_screen"]
        assert calls["stop_everything"] < calls["_closing_screen"]


class TestWaitingForTheRoom:
    def test_the_marker_landing_ends_the_wait(self, tmp_path):
        marker = scene_ready_file(tmp_path)
        marker.write_text("", encoding="utf-8")

        assert _wait_for_the_room(marker, _AliveFunestra())

    def test_a_funestra_that_died_is_not_waited_for(self, tmp_path):
        assert not _wait_for_the_room(scene_ready_file(tmp_path), _AliveFunestra(alive=False))

    def test_the_cap_gives_up_rather_than_hangs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(orchestrator, "SCENE_READY_TIMEOUT_S", 0.0)

        assert not orchestrator._wait_for_the_room(
            scene_ready_file(tmp_path), _AliveFunestra())

    def test_a_room_that_never_reported_is_revealed_rather_than_failed(self):
        tree = ast.parse(_the_session_as_one_source())
        (call,) = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and ast.unparse(n.func) == "_wait_for_the_room"]
        discarded = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Expr) and n.value is call]

        assert discarded, "the launch branches on a wait that is meant to be advisory"

    def test_the_loading_screen_waits_past_the_first_status_for_frames_in_the_headset(self):
        calls = _call_lines_in_run_vr_bridge()

        assert calls["_wait_for_funestra"] < calls["_wait_for_the_room"]
        assert calls["_wait_for_the_room"] < calls["progress.finish"]

    def test_a_previous_sessions_marker_cannot_vouch_for_this_one(self):
        source = _the_session_as_one_source()

        assert "room_ready_file.unlink(missing_ok=True)" in source
        assert source.index("room_ready_file.unlink") < source.index("launch_vr_funestras(")


class TestEscDuringTheMinutesAColdHeadsetTakes:
    class _Cancelled:
        cancelled = True

        def advance(self, phase):
            pass

        def finish(self):
            pass

    def test_the_wait_for_the_funestra_gives_up_at_once(self, tmp_path):
        with pytest.raises(StartupCancelled):
            _wait_for_funestra(tmp_path / "main_player_status.txt", _AliveFunestra(), self._Cancelled())

    def test_the_wait_for_the_room_gives_up_at_once(self, tmp_path):
        with pytest.raises(StartupCancelled):
            _wait_for_the_room(scene_ready_file(tmp_path), _AliveFunestra(), self._Cancelled())

    def test_both_waits_are_handed_the_reporter_that_carries_the_flag(self):
        tree = ast.parse(_the_session_as_one_source())
        waits = [ast.unparse(n) for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and ast.unparse(n.func) in ("_wait_for_funestra", "_wait_for_the_room")]

        assert len(waits) == 2
        assert all(call.endswith(", progress)") for call in waits), waits


class TestWhatAPreviousSessionLeft:
    def test_its_files_are_gone_before_the_hotkey_script_goes_up(self, config):
        class HotkeyScriptWentUp(Exception):
            pass

        snapshot = config.dashboard_state_file
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text("[session]\nvr=0\n", encoding="utf-8")
        there_when_it_went_up: list[bool] = []

        def hotkey_script(*args, **kwargs):
            there_when_it_went_up.append(snapshot.exists())
            raise HotkeyScriptWentUp

        with patch.object(orchestrator, "open_event_log"), \
             patch.object(orchestrator, "log_into_the_hotkey_scripts_log"), \
             patch("fun_time_vr.orchestrator.subprocess.Popen", side_effect=hotkey_script), \
             pytest.raises(HotkeyScriptWentUp):
            orchestrator.run_vr_bridge(config, SessionEnvironment())

        assert there_when_it_went_up == [False]


class TestTheHotkeyScriptsArguments:
    @staticmethod
    def _launched(config) -> list[str]:
        class HotkeyScriptWentUp(Exception):
            pass

        launched: list[list[str]] = []

        def hotkey_script(command, **_kwargs):
            launched.append(list(command))
            raise HotkeyScriptWentUp

        with patch.object(orchestrator, "open_event_log"), \
             patch.object(orchestrator, "log_into_the_hotkey_scripts_log"), \
             patch("fun_time_vr.orchestrator.subprocess.Popen", side_effect=hotkey_script), \
             pytest.raises(HotkeyScriptWentUp):
            orchestrator.run_vr_bridge(config, SessionEnvironment())
        return launched[0]

    def test_a_script_that_can_outlive_a_crossing_is_told_which_orchestrator_to_watch(self, config):
        assert self._launched(config)[-1] == str(os.getpid())

    def test_it_is_the_desks_own_script_so_esc_pauses_the_room_in_the_headset_too(self, config):
        assert Path(self._launched(config)[1]) == config.project_dir / HOTKEY_SCRIPT_NAME


def _launch_stand_ins(orchestrator, torn_down: list, **overrides):
    stand_ins = {
        "open_event_log": MagicMock(),
        "log_into_the_hotkey_scripts_log": MagicMock(),
        "leave_a_park_for_the_brokers_next_tick": MagicMock(),
        "ensure_broker": MagicMock(),
        "reap_orphaned_satellites": MagicMock(),
        "stock_the_playlists": MagicMock(side_effect=lambda _manifest, **asked: asked["carried"]),
        "launch_audio_companion": MagicMock(return_value=MagicMock(pid=101)),
        "launch_vr_funestras": MagicMock(return_value=MagicMock(pid=202)),
        "process_started": MagicMock(return_value=7),
        "_wait_for_funestra": MagicMock(return_value=True),
        "_wait_for_the_room": MagicMock(),
        "start_hud_priming": MagicMock(return_value=None),
        "stop_hotkey_script": MagicMock(side_effect=lambda _proc, _file: torn_down.append("hotkeys")),
        "kill_recorded_child": MagicMock(side_effect=lambda child: torn_down.append(child.pid)),
        "_release_vr_runtime": MagicMock(side_effect=lambda *_a, **_k: torn_down.append("runtime")),
    }
    stand_ins.update(overrides)
    return patch.multiple(orchestrator, **stand_ins)


def _end_a_vr_session(
    orchestrator, config, *, ended_by, runtime_was_running=True, **overrides,
) -> list[str]:
    config.paths.state_dir.mkdir(parents=True, exist_ok=True)
    offered: list[str] = []
    real_closing_screen = orchestrator._closing_screen

    def closing_screen(*args, **kwargs):
        offered.append(kwargs.get("esc_cancels", ""))
        return real_closing_screen(*args, **kwargs)

    funestras = MagicMock(pid=202)
    funestras.poll.return_value = None
    stand_ins = {
        "launch_vr_funestras": MagicMock(return_value=funestras),
        "wait_for_loading_screen_painted": MagicMock(return_value=True),
        "DispatchLoopRunner": MagicMock(),
        "start_voice_control": MagicMock(return_value=(None, None)),
        "_wait_for_session_end": MagicMock(side_effect=ended_by),
        "_closing_screen": closing_screen,
    }
    stand_ins.update(overrides)
    with _launch_stand_ins(orchestrator, [], **stand_ins), \
         patch.object(orchestrator.vr_runtime, "runtime_was_running",
                      return_value=runtime_was_running), \
         patch("fun_time_vr.orchestrator.subprocess.Popen"):
        orchestrator.run_vr_bridge(config, SessionEnvironment())
    return offered


def _asked_to_cross(config):
    def ended(*_args, **_kwargs):
        request_handoff(config.paths.state_dir, DESKTOP)
        return "asked"

    return ended


def _asked_then_esc(config):
    def ended(*_args, **_kwargs):
        (config.paths.state_dir / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")
        return "asked"

    return ended


class TestOpeningAVrSession:
    def test_an_origenerator_that_never_comes_up_ends_the_launch_and_says_why(self, config):
        config.paths.state_dir.mkdir(parents=True, exist_ok=True)
        torn_down: list = []
        said: list[str] = []

        with _launch_stand_ins(
            orchestrator, torn_down,
            finish_under_the_loading_screen=MagicMock(side_effect=OrigeneratorNeverCameUp),
            show_funestra_died_alert=said.append,
        ), patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=True), \
             patch("fun_time_vr.orchestrator.subprocess.Popen"):
            exit_code = orchestrator.run_vr_bridge(config, SessionEnvironment())

        assert exit_code == 1
        assert said == [origenerator_never_came_up_message()]
        assert torn_down == ["hotkeys", 101, 202, "runtime"]

    def test_a_failure_takes_down_what_it_launched_and_gives_the_monitors_back(self, config):
        config.paths.state_dir.mkdir(parents=True, exist_ok=True)
        torn_down: list = []

        with _launch_stand_ins(
            orchestrator, torn_down,
            DispatchLoopRunner=MagicMock(side_effect=TypeError("a caller the last refactor missed")),
        ), patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=True), \
             patch("fun_time_vr.orchestrator.subprocess.Popen"), \
             pytest.raises(TypeError):
            orchestrator.run_vr_bridge(config, SessionEnvironment())

        assert torn_down == ["hotkeys", 101, 202, "runtime"]
        assert pending_handoff(config.paths.state_dir) is None

    def test_a_funestra_that_never_comes_up_is_ended_with_the_loading_screen_kept_up(self, config):
        config.paths.state_dir.mkdir(parents=True, exist_ok=True)
        heard: list[bool] = []

        def the_audio_companion_takes_its_time(child):
            if child.pid == 101:
                heard.append(heard_from_again_once_stale(
                    config.paths.state_dir / PROGRESS_FILENAME,
                    stale_after_s=STARTUP_STALE_TIMEOUT_S))

        with _launch_stand_ins(
            orchestrator, [], _wait_for_funestra=MagicMock(return_value=False),
            kill_recorded_child=MagicMock(side_effect=the_audio_companion_takes_its_time),
        ), patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=True), \
             patch("fun_time_vr.orchestrator.subprocess.Popen"):
            orchestrator.run_vr_bridge(config, SessionEnvironment())

        assert heard == [True]

    def test_the_waits_for_the_headset_and_its_room_keep_the_loading_screen_up(self, config):
        heard: list[bool] = []

        def waits(result):
            def wait(*_args, **_kwargs):
                heard.append(heard_from_again_once_stale(
                    config.paths.state_dir / PROGRESS_FILENAME,
                    stale_after_s=STARTUP_STALE_TIMEOUT_S))
                return result
            return wait

        _end_a_vr_session(
            orchestrator, config, ended_by=lambda *_a, **_k: "asked",
            _wait_for_funestra=MagicMock(side_effect=waits(True)),
            _wait_for_the_room=MagicMock(side_effect=waits(True)),
        )

        assert heard == [True, True], (
            "the two waits add up past the headset's loading screen's patience")

    def test_a_session_he_quit_says_esc_cancels_closing_fun_time_vr(self, config):
        offered = _end_a_vr_session(orchestrator, config, ended_by=lambda *_a, **_k: "asked")

        assert offered == ["Press Esc to cancel closing Fun Time VR"]

    def test_a_session_he_quit_stops_the_robot_hand_and_parks_the_osr2(self, config):
        _end_a_vr_session(orchestrator, config, ended_by=lambda *_a, **_k: "asked")

        assert config.genau_cmd_file.read_text(encoding="utf-8").splitlines()[-1] == "PAUSE"
        assert config.broker_cmd_file.read_text(encoding="utf-8").split() == ["PARK"]

    def test_a_funestra_that_dies_mid_session_leaves_its_exit_code_in_the_log(self, config, caplog):
        died = MagicMock(pid=202, returncode=3221225477)
        died.poll.return_value = None

        with caplog.at_level(logging.INFO, logger="fun_time_vr.orchestrator"):
            _end_a_vr_session(orchestrator, config, ended_by=lambda *_a, **_k: "funestras",
                              launch_vr_funestras=MagicMock(return_value=died))

        assert "3221225477" in caplog.text

    def test_a_session_that_brings_the_runtime_up_remembers_the_sound_devices_first(
            self, config):
        happened: list[str] = []
        funestras = MagicMock(pid=202)
        funestras.poll.return_value = None
        with patch.object(orchestrator.vr_runtime, "remember_the_sound_devices",
                          side_effect=happened.append):
            _end_a_vr_session(
                orchestrator, config, ended_by=lambda *_a, **_k: "asked",
                runtime_was_running=False,
                launch_vr_funestras=MagicMock(
                    side_effect=lambda **_k: (happened.append("funestras"), funestras)[1]))

        assert happened == [config.paths.state_dir, "funestras"]

    def test_a_session_on_a_runtime_that_was_already_up_remembers_nothing(self, config):
        with patch.object(orchestrator.vr_runtime, "remember_the_sound_devices") as remembered:
            _end_a_vr_session(orchestrator, config, ended_by=lambda *_a, **_k: "asked",
                              runtime_was_running=True)

        remembered.assert_not_called()

    def test_whether_the_runtime_was_up_is_read_before_the_headset_process_wakes_it(self):
        calls = _call_lines_in_run_vr_bridge()

        assert calls["vr_runtime.runtime_was_running"] < calls["launch_vr_funestras"]
        assert "vr_runtime.ensure_ready()" in inspect.getsource(app.main)

    def test_a_launch_canceled_before_the_headsets_phase_stops_no_runtime(self, config):
        config.paths.state_dir.mkdir(parents=True, exist_ok=True)
        stopped: list = []

        with _launch_stand_ins(
            orchestrator, [],
            launch_audio_companion=MagicMock(side_effect=StartupCancelled()),
            _release_vr_runtime=orchestrator._release_vr_runtime,
        ), patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=False), \
             patch.object(orchestrator.vr_runtime, "stop_the_runtime_a_session_started",
                          stopped.append), \
             patch("fun_time_vr.orchestrator.subprocess.Popen"):
            orchestrator.run_vr_bridge(config, SessionEnvironment())

        assert stopped == []

    def test_the_runtime_goes_after_the_funestra_whose_xr_session_it_held(self, config):
        order: list = []

        _end_a_vr_session(
            orchestrator, config, ended_by=lambda *_a, **_k: "asked",
            kill_recorded_child=MagicMock(side_effect=lambda child: order.append(child.pid)),
            _release_vr_runtime=MagicMock(side_effect=lambda *_a, **_k: order.append("runtime")),
        )

        assert order[-2:] == [202, "runtime"]

    def test_a_session_crossing_to_fun_time_says_esc_cancels_exiting_vr(self, config):
        def asked_to_cross(*_args, **_kwargs):
            request_handoff(config.paths.state_dir, DESKTOP)
            return "asked"

        offered = _end_a_vr_session(
            orchestrator, config, ended_by=asked_to_cross,
            _wait_for_the_headset_hold=MagicMock(return_value=True),
        )

        assert offered == ["Press Esc to cancel exiting VR"]

    def test_over_a_crossing_the_hotkey_script_stays_to_hear_esc_for_the_relay(self, config):
        def asked_to_cross(*_args, **_kwargs):
            request_handoff(config.paths.state_dir, DESKTOP)
            return "asked"

        stopped = MagicMock()
        _end_a_vr_session(orchestrator, config, ended_by=asked_to_cross,
                          _wait_for_the_headset_hold=MagicMock(return_value=True),
                          stop_hotkey_script=stopped)

        stopped.assert_not_called()

    def test_esc_on_the_closing_screen_opens_fun_time_vr_again_offering_no_esc(self, config):
        _end_a_vr_session(
            orchestrator, config, ended_by=_asked_then_esc(config),
            _wait_for_the_headset_hold=MagicMock(return_value=True),
        )

        taken = take_handoff_request(config.paths.state_dir)
        assert taken is not None, "Esc let the quit go on"
        assert (taken.target, taken.cancelable) == (VR, False)

    def test_the_way_backs_only_screen_can_import_the_sessions_siblings(self, config):
        sibling = config.paths.state_dir.parent / "sibling_checkout"
        sibling.mkdir(parents=True, exist_ok=True)
        named = replace(config, paths=replace(config.paths, sibling_checkouts=(sibling,)))
        way_back = MagicMock()

        _end_a_vr_session(
            orchestrator, named, ended_by=_asked_then_esc(named),
            _wait_for_the_headset_hold=MagicMock(return_value=True),
            launch_the_way_back_screen=way_back,
        )

        assert way_back.call_args.kwargs["sibling_checkouts"] == str(sibling)

    def test_on_the_way_back_into_vr_the_headset_stays_covered_and_the_runtime_up(
        self, config,
    ):
        asked = MagicMock()
        _end_a_vr_session(orchestrator, config, ended_by=_asked_then_esc(config),
                          hold_the_headset=asked,
                          _wait_for_the_headset_hold=MagicMock(return_value=True))

        asked.assert_called_once_with(config.paths.state_dir, stop_runtime=False)

    def test_a_refused_hold_on_the_way_back_into_vr_leaves_the_runtime_running(
        self, config,
    ):
        released: list = []
        _end_a_vr_session(orchestrator, config, ended_by=_asked_then_esc(config),
                          _wait_for_the_headset_hold=MagicMock(return_value=False),
                          _release_vr_runtime=released.append)

        assert released == []

    def test_on_the_way_back_into_vr_both_covers_say_it_is_canceling(self, config):
        _end_a_vr_session(orchestrator, config, ended_by=_asked_then_esc(config),
                          _wait_for_the_headset_hold=MagicMock(return_value=True))

        line = parse_progress(
            crossing_progress_path(config.paths.state_dir).read_text(encoding="utf-8"))
        assert (line.message, line.hint) == (CANCELING, "")

    def test_the_funestras_long_letting_go_runs_beside_the_controls_coming_down(self, config):
        controls = MagicMock()
        asked_by_then: list[bool] = []
        controls.stop.side_effect = lambda: asked_by_then.append(
            headset_hold_asked(config.paths.state_dir))

        _end_a_vr_session(
            orchestrator, config, ended_by=_asked_to_cross(config),
            start_voice_control=MagicMock(return_value=(controls, None)),
            _wait_for_the_headset_hold=MagicMock(return_value=True),
        )

        assert asked_by_then == [True]

    def test_a_crossing_leaves_the_runtime_it_started_to_the_funestra_outliving_it(self, config):
        asked = MagicMock()
        _end_a_vr_session(
            orchestrator, config, ended_by=_asked_to_cross(config),
            runtime_was_running=False,
            hold_the_headset=asked,
            _wait_for_the_headset_hold=MagicMock(return_value=True),
        )

        asked.assert_called_once_with(config.paths.state_dir, stop_runtime=True)

    def test_on_the_way_back_into_vr_the_hosted_app_stays_parked(self, config):
        parked: list = []
        _end_a_vr_session(
            orchestrator, config, ended_by=_asked_then_esc(config),
            _wait_for_the_headset_hold=MagicMock(return_value=True),
            bring_up_the_hosted_app=MagicMock(
                return_value=HostedApp(6060, already_open=True, taken_over=False,
                                       showing_a_slideshow=False)),
            see_the_hosted_app_out=MagicMock(
                side_effect=lambda *a, **kw: parked.append(kw["keep"]) or True))

        assert parked == [True], "the app was not left for the session coming back"

    def test_voice_starts_the_way_a_desktop_session_starts_it(self, config):
        config.paths.state_dir.mkdir(parents=True, exist_ok=True)
        runner = MagicMock()
        start_voice = MagicMock(return_value=(None, None))
        prepared = object()
        order: list[str] = []
        prepare_voice = MagicMock(side_effect=lambda _path, **_asking: order.append("voice") or prepared)

        with _launch_stand_ins(
            orchestrator, [],
            DispatchLoopRunner=runner,
            prepare_voice_control=prepare_voice,
            launch_vr_funestras=MagicMock(
                side_effect=lambda **_kwargs: order.append("funestras") or MagicMock(pid=202)),
            start_voice_control=start_voice,
            _wait_for_session_end=MagicMock(return_value="ahk"),
        ), patch.object(orchestrator.vr_runtime, "runtime_was_running", return_value=True), \
             patch("fun_time_vr.orchestrator.subprocess.Popen"):
            orchestrator.run_vr_bridge(config, SessionEnvironment())

        manifest = LaunchManifest.read(config.paths.state_dir / "windows_bridge_launch.ini")
        assert order == ["voice", "funestras"], "voice was prepared after the Funestra launched"
        [(said, asking)] = [(call.args, call.kwargs) for call in prepare_voice.call_args_list]
        assert said == (manifest.runtime.config_path,)
        assert asking["ask_the_hosted_app"].args == (manifest,)
        assert asking["dashboard_cmd_file"] == Path(manifest.commands.dashboard_cmd_file)
        assert asking["hosted_cmd_file"] == Path(manifest.commands.origenerator_cmd_file)
        assert asking["hosted_said_at_suffix"] == build_bridge_config_from_manifest(
            manifest).origenerator_said_at_suffix
        start_voice.assert_called_once_with(prepared, dispatch_runner=runner.return_value)

    def test_the_headsets_loading_screen_says_voice_commands_are_starting_until_they_listen(
            self, config):
        progress_file = config.paths.state_dir / PROGRESS_FILENAME
        seen: list[str] = []
        controller = MagicMock()
        controller.done_starting.wait.side_effect = lambda _timeout: seen.append(
            parse_progress(progress_file.read_text(encoding="utf-8")).message) or True

        _end_a_vr_session(orchestrator, config, ended_by=lambda *_a, **_k: "asked",
                          start_voice_control=MagicMock(return_value=(controller, None)))

        assert seen == [STARTING_VOICE_COMMANDS.message]
