from __future__ import annotations

import configparser
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from app_support import origenerator_handoff as handoff
from app_support import ports
from app_support.origenerator_handoff import hand_over, take_the_takeover
from app_support.testing import origenerator_handed_over
from app_support.win32 import process_started
from funestra.modes import MainMode, SatellitesMode

from fun_time import windows_bridge_sequencer
from fun_time.config import LayoutConfig, load_config
from fun_time.dashboard_layout import dashboard_window_height
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.funestra_deaths import FunestraDied, LaunchedFunestra
from fun_time.funestra_status import (
    read_main_funestra_status,
)
from fun_time.main_funestra_hud import main_funestra_hud_path
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    LaunchManifest,
    RandomFavesBrowserSettings,
    write_windows_bridge_manifest,
)
from fun_time.monitors import MonitorInfo
from fun_time.origenerator_switches import RfbMode
from fun_time.overlay_progress import (
    NullProgress,
    StartupCancelled,
)
from fun_time.session_environment import SessionEnvironment
from fun_time.session_handoff import keep_the_origenerator
from fun_time.shared_state import (
    BridgeState,
    SatelliteState,
    shared_state_path,
    write_shared_state,
)
from fun_time.shortcuts import Shortcut
from fun_time.window_layout import (
    MonitorRect,
    WindowLayoutPlan,
    WindowRect,
    compute_window_layout,
    screen_layout,
)
from fun_time.windows_bridge_sequencer import (
    _PLAY_POLL_S,
    SATELLITE_PLAY_TIMEOUT_S,
    _maybe_launch_random_faves_browser,
    _resolve_satellite_hwnds,
    _wait_for_funestras_drawing,
    _wait_for_main_funestra_loaded,
    release_the_funestras,
    run_startup_sequence,
)
from fun_time.windows_bridge_startup import GenauStart, HandedFunestra
from tests.git_checkouts import a_worktree_of
from tests.sleeps import sleeps_in

FAKE_MONITORS = [
    MonitorInfo(x=0, y=0, width=2560, height=1392),
    MonitorInfo(x=2560, y=0, width=1440, height=3440),
]


def fake_screen_layout(layout_config, **strip):
    with patch("fun_time.window_layout.enumerate_monitors", return_value=FAKE_MONITORS):
        return screen_layout(layout_config, **strip)

CORE_PIDS = {"portrait_pid": 30, "landscape_pid": 40}
UI_PIDS = {"dashboard_pid": 50, "audio_pid": 70}
MAIN_FUNESTRA_PID = 25

SECONDARY_MONITOR = FAKE_MONITORS[1]
PORTRAIT_HEIGHT_AT_THE_CONFTESTS_MAIN_TOP_RATIO = 2500
PRIMARY_MEDIA_RECT = {
    "x": SECONDARY_MONITOR.x, "y": PORTRAIT_HEIGHT_AT_THE_CONFTESTS_MAIN_TOP_RATIO,
    "width": SECONDARY_MONITOR.width,
    "height": SECONDARY_MONITOR.height - PORTRAIT_HEIGHT_AT_THE_CONFTESTS_MAIN_TOP_RATIO,
}


def _make_manifest(cfg_factory, tmp_path, overrides: dict | None = None):
    cfg = load_config(cfg_factory(overrides))
    manifest_path = write_windows_bridge_manifest(
        cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    return cfg, manifest_path


def _pause_every_funestra(m) -> list[Path]:
    flags = [Path(m.commands.main_funestra_paused_file), Path(m.commands.genau_paused_file),
             Path(m.commands.audio_paused_file)]
    for flag in flags:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("1", encoding="utf-8")
    return flags


def _write_result(result_file, values):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["result"] = {k: str(v) for k, v in values.items()}
    path = Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        parser.write(fp)


def _fake_core(m, **kwargs):
    _write_result(kwargs["result_file"], CORE_PIDS)
    return "kino"


def _name_a_sibling_checkout(manifest_path, checkout: Path) -> Path:
    checkout.mkdir()
    manifest = configparser.ConfigParser()
    manifest.optionxform = str
    manifest.read(manifest_path, encoding="utf-8")
    manifest["runtime"]["sibling_checkouts"] = str(checkout)
    with Path(manifest_path).open("w", encoding="utf-8") as fp:
        manifest.write(fp)
    return checkout


def _fake_core_in(mode: MainMode):
    def launch(m, **kwargs):
        _write_result(kwargs["result_file"], CORE_PIDS)
        return mode
    return launch


def _seed_paused_flags(manifest_path) -> dict[str, Path]:
    m = configparser.ConfigParser()
    m.optionxform = str
    m.read(str(manifest_path), encoding="utf-8")
    flags = {}
    for key in ("main_funestra_paused_file", "genau_paused_file", "audio_paused_file"):
        path = Path(m["commands"][key])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
        flags[key] = path
    return flags


def _run_revealing_sequence(manifest_path, tmp_path, mode: str) -> None:
    with _sequencer_stubs(
            start_core_session={"side_effect": _fake_core_in(mode)},
            wait_for_window_by_title={"return_value": 88888}):
        run_startup_sequence(
            manifest_path=manifest_path, state_dir=tmp_path, hide_windows=False,
        )


SEQUENCER = "fun_time.windows_bridge_sequencer"

_STUB_MODULES = {"launch_origenerator": "fun_time.hosted_origenerator",
                 "is_process_alive": "fun_time.funestra_deaths"}


@contextlib.contextmanager
def _sequencer_stubs(**overrides):
    spec: dict[str, dict] = {
        "start_core_session": {"side_effect": _fake_core},
        "launch_main_funestra": {"side_effect": _fake_main_funestra},
        "launch_ui_companions": {"side_effect": _fake_ui},
        "screen_layout": {"side_effect": fake_screen_layout},
        "wait_for_window_by_title": {"return_value": 99999},
        "move_window": {},
        "set_always_on_top": {},
        "is_process_alive": {"return_value": True},
    }
    spec.update(overrides)
    with contextlib.ExitStack() as stack:
        mocks = SimpleNamespace()
        for name, kwargs in spec.items():
            setattr(mocks, name, stack.enter_context(
                patch(f"{_STUB_MODULES.get(name, SEQUENCER)}.{name}", **kwargs)))
        stack.enter_context(patch(
            "fun_time.windows_bridge_sequencer.time",
            _a_frozen_clock_that_fails_any_new_time_call()))
        yield mocks


def _a_frozen_clock_that_fails_any_new_time_call():
    return SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _s: None)


def _fake_ui(m, **kwargs):
    _write_result(kwargs["result_file"], UI_PIDS)


def _write_the_status_the_reveal_waits_for(m):
    Path(m.commands.main_funestra_status_file).write_text("video=this_session.mp4\n", encoding="utf-8")


def _fake_main_funestra(m, **kwargs):
    _write_the_status_the_reveal_waits_for(m)
    return MAIN_FUNESTRA_PID


ORIGENERATOR_PID = 91


def _fake_origenerator(**kwargs):
    Path(kwargs["session"].status_file).write_text(
        "portrait_active=1\nportrait_video=\nportrait_locked=0\n"
        "landscape_active=1\nlandscape_video=\nlandscape_locked=0\n",
        encoding="utf-8",
    )
    return ORIGENERATOR_PID


def test_phase_result_files_are_unique_per_launch(tmp_path, monkeypatch):
    two_readings_where_the_frozen_clock_gives_one = iter([12.001, 47.002])
    monkeypatch.setattr(windows_bridge_sequencer, "time", SimpleNamespace(
        monotonic=lambda: next(two_readings_where_the_frozen_clock_gives_one)))

    first = windows_bridge_sequencer._build_unique_result_path(tmp_path, "phase1")
    second = windows_bridge_sequencer._build_unique_result_path(tmp_path, "phase1")

    assert first != second
    assert first.parent == tmp_path
    assert first.name.startswith("phase1_")


class TestRunStartupSequence:
    def _captured_launch(self, cfg_factory, tmp_path, *, paths=None, **stubs):
        cfg = load_config(cfg_factory({
            "regen": {
                "media_root": str(tmp_path / "media"),
                "metadata_root": str(tmp_path / "metadata"),
            },
            **({"paths": paths} if paths else {}),
        }))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        core_called = {}
        ui_called = {}

        def capture_core(m, **kwargs):
            core_called.update(kwargs, m=m)
            _write_result(kwargs["result_file"], CORE_PIDS)

        def capture_ui(m, **kwargs):
            ui_called.update(kwargs, m=m)
            _write_result(kwargs["result_file"], UI_PIDS)

        with _sequencer_stubs(start_core_session={"side_effect": capture_core},
                              launch_ui_companions={"side_effect": capture_ui}, **stubs):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)
        return cfg, result, core_called, ui_called

    def test_every_phases_pids_are_gathered_into_the_launch_result(self, cfg_factory, tmp_path):
        _cfg, result, _core, _ui = self._captured_launch(cfg_factory, tmp_path)

        assert result.main_funestra_pid == MAIN_FUNESTRA_PID
        assert result.portrait_pid == 30
        assert result.landscape_pid == 40
        assert result.dashboard_pid == 50
        assert result.audio_pid == 70

    def test_the_satellites_are_wired_from_the_manifest(self, cfg_factory, tmp_path):
        cfg, _result, core_called, _ui = self._captured_launch(cfg_factory, tmp_path)

        assert core_called["m"].executables.python_exe == str(cfg.paths.python_exe)
        assert core_called["m"].modules.satellite_module == "satellite"
        state = cfg.paths.state_dir
        for satellite in ("portrait", "landscape"):
            launch = core_called[satellite]
            assert launch.funestra.label == satellite
            assert launch.channels.command == Path(state / f"{satellite}_cmd.txt")
            assert launch.channels.paused == Path(state / f"{satellite}_paused.txt")
            assert launch.channels.status == Path(state / f"{satellite}_status.txt")
            assert launch.log_file == tmp_path / f"{satellite}_satellite.log"
            assert launch.channels.playlist == tmp_path / f"{satellite}_playlist.tsv"
        assert core_called["m"].commands.main_funestra_status_file == str(cfg.main_funestra_status_file)
        assert core_called["m"].commands.main_funestra_paused_file == str(cfg.main_funestra_paused_file)

    def test_the_satellites_launch_straight_into_their_layout_rects(self, cfg_factory, tmp_path):
        _cfg, _result, core_called, ui_called = self._captured_launch(cfg_factory, tmp_path)

        assert core_called["portrait"].rect.x == 2560
        assert core_called["portrait"].rect.width > 0
        assert core_called["landscape"].rect != core_called["portrait"].rect
        assert ui_called["plan"].dashboard.width > 0

    def test_the_core_launch_carries_the_session_files_through(self, cfg_factory, tmp_path):
        cfg, _result, core_called, _ui = self._captured_launch(cfg_factory, tmp_path)

        assert core_called["state_dir"] == tmp_path
        commands = core_called["m"].commands
        assert Path(core_called["m"].regen.metadata_root) == tmp_path / "metadata", (
            "the startup's playlist build collapses action groups from it")
        assert commands.broker_state_dir == str(cfg.paths.broker_state_dir), (
            "startup leaves a Broker found running there alone")
        assert commands.broker_cmd_file == str(cfg.broker_cmd_file), (
            "startup parks the OSR2 through it for the long wait")

    def test_the_ui_launch_enables_the_dashboard_and_forwards_the_rfb_rect(self, cfg_factory, tmp_path):
        _cfg, _result, _core, ui_called = self._captured_launch(cfg_factory, tmp_path)

        assert ui_called["m"].dashboard_enabled is True
        assert all(
            isinstance(value, int) for value in ui_called["plan"].random_faves_browser)

    def test_a_session_hosting_origenerator_opens_the_dashboard_tall_enough_for_its_strip(
            self, cfg_factory, tmp_path):
        _cfg, _result, _core, ui_called = self._captured_launch(
            cfg_factory, tmp_path, paths={"origenerator_dir": str(tmp_path / "origenerator")},
            launch_origenerator={"side_effect": _fake_origenerator})

        dashboard = ui_called["plan"].dashboard
        assert dashboard.height == dashboard_window_height(rfb_mode_selector=True)
        assert ui_called["plan"].random_faves_browser.y == dashboard.y + dashboard.height

    def test_launches_the_main_funestra_with_the_primary_media_rect_and_genaus_files(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        main_funestra_kwargs = {}

        def capture_main_funestra(m, **kwargs):
            main_funestra_kwargs.update(kwargs, m=m)
            return _fake_main_funestra(m, **kwargs)

        with _sequencer_stubs(launch_main_funestra={"side_effect": capture_main_funestra}, wait_for_window_by_title={"return_value": 88888}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        given = main_funestra_kwargs["m"]
        assert given == LaunchManifest.read(manifest_path)
        assert given.executables.python_exe == str(cfg.paths.python_exe)
        assert given.modules.main_funestra_module == "main_funestra"
        assert given.runtime.config_path == str(cfg.config_path)
        commands = given.commands
        assert commands.main_funestra_playlist_file == str(cfg.main_funestra_playlist_file)
        assert commands.main_funestra_cmd_file == str(cfg.main_funestra_cmd_file)
        assert commands.main_funestra_paused_file == str(cfg.main_funestra_paused_file)
        assert commands.main_funestra_status_file == str(cfg.main_funestra_status_file)
        assert commands.main_funestra_console_file == str(main_funestra_hud_path(cfg.paths.state_dir))
        assert commands.genau_drive_file == str(Path(cfg.genau_cmd_file).parent / "genau_drive.txt")
        assert commands.genau_cmd_file == str(cfg.genau_cmd_file)
        assert commands.genau_paused_file == str(cfg.genau_paused_file)
        assert commands.genau_status_file == str(cfg.genau_status_file)
        assert given.media.genau_flicks == str(cfg.paths.flicks_dir)
        assert given.regen.metadata_root == ""
        assert commands.main_funestra_notice_file == str(cfg.main_funestra_notice_file)
        assert commands.dashboard_cmd_file == str(cfg.paths.state_dir / "dashboard_cmd.txt")
        assert commands.osr2_slot_port == str(ports.OSR2_SLOT)
        assert given.runtime.sibling_checkouts == ""
        assert main_funestra_kwargs["log_file"] == tmp_path / "main_funestra.log"
        assert main_funestra_kwargs["state_dir"] == tmp_path, (
            "left to itself it keeps its mode and play points beside its config, in the checkout")
        assert main_funestra_kwargs["rect"] == WindowRect(**PRIMARY_MEDIA_RECT)
        assert main_funestra_kwargs["genau"] == GenauStart()

    def test_the_main_funestra_starts_from_fun_times_own_python_whatever_genau_python_the_config_names(
            self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path, {
            "paths": {"genau_python_exe": str(tmp_path / "genau" / "pythonw.exe")}})
        main_funestra_kwargs: dict = {}

        def capture_main_funestra(m, **kwargs):
            main_funestra_kwargs.update(kwargs, m=m)
            return _fake_main_funestra(m, **kwargs)

        with _sequencer_stubs(launch_main_funestra={"side_effect": capture_main_funestra}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert main_funestra_kwargs["m"].executables.python_exe == str(cfg.paths.python_exe)

    def test_the_main_funestra_reads_every_setting_from_fun_times_own_config(
            self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        main_funestra_kwargs: dict = {}

        def capture_main_funestra(m, **kwargs):
            main_funestra_kwargs.update(kwargs, m=m)
            return _fake_main_funestra(m, **kwargs)

        with _sequencer_stubs(launch_main_funestra={"side_effect": capture_main_funestra}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert main_funestra_kwargs["m"].runtime.config_path == str(cfg.config_path)
        assert main_funestra_kwargs["m"].media.genau_flicks == str(cfg.paths.flicks_dir)

    def test_the_main_funestra_starts_with_the_named_checkouts(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        checkout = _name_a_sibling_checkout(manifest_path, tmp_path / "a_worktree")
        main_funestra_kwargs: dict = {}

        def capture_main_funestra(m, **kwargs):
            main_funestra_kwargs.update(kwargs, m=m)
            return _fake_main_funestra(m, **kwargs)

        with _sequencer_stubs(launch_main_funestra={"side_effect": capture_main_funestra}, wait_for_window_by_title={"return_value": 88888}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert main_funestra_kwargs["m"].runtime.sibling_checkouts == str(checkout)

    def test_the_satellites_import_funestra_from_the_checkouts_the_session_names(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        checkout = _name_a_sibling_checkout(manifest_path, tmp_path / "a_worktree")

        with _sequencer_stubs() as stubs:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert stubs.start_core_session.call_args.args[0].runtime.sibling_checkouts == str(checkout)

    def test_positions_satellite_windows_and_applies_topmost_policy(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        title_to_hwnd = {
            "Main Funestra": 2525,
            "Portrait AI Funestra": 3030,
            "Landscape AI Funestra": 4040,
        }
        move_calls: list[tuple] = []
        topmost_calls: list[tuple] = []

        with _sequencer_stubs(wait_for_window_by_title={"side_effect": lambda title, **kw: title_to_hwnd.get(title, 0)}, move_window={"side_effect": lambda hwnd, rect, **_kw: move_calls.append((hwnd, *rect))}, set_always_on_top={"side_effect": lambda h, v, **_kw: topmost_calls.append((h, v))}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        moved_hwnds = {c[0] for c in move_calls}
        assert {3030, 4040} <= moved_hwnds

        promoted = {h for h, on in topmost_calls if on}
        assert promoted == {3030, 4040, 2525}

    def test_non_hidden_path_unpauses_main_funestra(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = configparser.ConfigParser()
        m.optionxform = str
        m.read(str(manifest_path), encoding="utf-8")
        main_funestra_paused = Path(m["commands"]["main_funestra_paused_file"])
        main_funestra_paused.parent.mkdir(parents=True, exist_ok=True)
        main_funestra_paused.write_text("1", encoding="utf-8")

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path, hide_windows=False)

        assert main_funestra_paused.read_text(encoding="utf-8").strip() == "0"

    def test_genau_is_launched_onto_the_video_it_was_left_showing(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        cfg.genau_status_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.genau_status_file.write_text(
            "flick=C:\\flicks\\alpha.mp4\n", encoding="utf-8",
        )

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}) as stubs:
            launch = stubs.launch_main_funestra

            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert launch.call_args.kwargs["genau"].start_flick == "C:\\flicks\\alpha.mp4"

    def test_genau_is_launched_in_the_order_it_was_left_browsing(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        write_shared_state(shared_state_path(tmp_path), BridgeState(genau_latest=True))

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}) as stubs:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert stubs.launch_main_funestra.call_args.kwargs["genau"].latest is True

    def test_genau_is_launched_in_the_f_mode_it_was_left_in_over_the_apps_favorites(
            self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        write_shared_state(shared_state_path(tmp_path), BridgeState(genau_favorites_filter=True))

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}) as stubs:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        given = stubs.launch_main_funestra.call_args
        manifest = LaunchManifest.read(manifest_path)
        assert given.kwargs["genau"].f_mode is True
        assert given.args[0].commands.main_funestra_notice_file == manifest.commands.main_funestra_notice_file

    def test_genau_is_launched_knowing_where_the_librarys_records_are(self, cfg_factory, tmp_path):
        records = tmp_path / "library" / "videos" / "metadata"
        cfg = load_config(cfg_factory({"regen": {"metadata_root": str(records)}}))
        manifest_path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}) as stubs:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert Path(stubs.launch_main_funestra.call_args.args[0].regen.metadata_root) == records

    def test_the_main_funestra_reaches_the_osr2_through_the_slot_the_config_names(
            self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({"osr2_slot_port": 50599}))
        manifest_path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}) as stubs:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert stubs.launch_main_funestra.call_args.args[0].commands.osr2_slot_port == "50599"

    def test_a_genau_session_bands_the_one_window_genau_and_kino_share(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        title_to_hwnd = {
            "Main Funestra": 2525,
            "Portrait AI Funestra": 3030, "Landscape AI Funestra": 4040,
        }
        topmost_calls: list[tuple] = []

        with _sequencer_stubs(start_core_session={"side_effect": _fake_core_in(MainMode.GENAU)}, wait_for_window_by_title={"side_effect": lambda title, **kw: title_to_hwnd.get(title, 0)}, set_always_on_top={"side_effect": lambda h, v, **_kw: topmost_calls.append((h, v))}):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert {h for h, on in topmost_calls if on} == {3030, 4040, 2525}
        assert result.main_mode is MainMode.GENAU

    def test_a_kino_session_is_revealed_by_starting_both(self, cfg_factory, tmp_path):
        paused = _seed_paused_flags(_make_manifest(cfg_factory, tmp_path)[1])

        _run_revealing_sequence(_make_manifest(cfg_factory, tmp_path)[1], tmp_path, "kino")

        assert paused["main_funestra_paused_file"].read_text(encoding="utf-8").strip() == "0"
        assert paused["genau_paused_file"].read_text(encoding="utf-8").strip() == "0"

    @pytest.mark.parametrize("mode", ["genau", "kino"])
    def test_the_reveal_leaves_the_hand_to_the_arbiter_in_either_mode(
        self, cfg_factory, tmp_path, mode,
    ):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        _seed_paused_flags(manifest_path)
        genau_cmd = Path(cfg.genau_cmd_file)
        genau_cmd.parent.mkdir(parents=True, exist_ok=True)
        genau_cmd.write_text("", encoding="utf-8")

        _run_revealing_sequence(manifest_path, tmp_path, mode)

        assert genau_cmd.read_text(encoding="utf-8") == "", (
            "a hand started here would be on the OSR2 against a funscript about to take it")

    def test_a_genau_reveal_leaves_kinos_video_held_under_genaus_flick(self, cfg_factory, tmp_path):
        paused = _seed_paused_flags(_make_manifest(cfg_factory, tmp_path)[1])

        _run_revealing_sequence(_make_manifest(cfg_factory, tmp_path)[1], tmp_path, "genau")

        assert paused["main_funestra_paused_file"].read_text(encoding="utf-8").strip() == "1", (
            "Kino's video playing under Genau's flick would be a second driver of the OSR2")
        assert paused["genau_paused_file"].read_text(encoding="utf-8").strip() == "0"
        assert paused["audio_paused_file"].read_text(encoding="utf-8").strip() == "0"

    def test_a_kino_session_opens_with_every_funestra_in_the_band(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        title_to_hwnd = {
            "Main Funestra": 2525,
            "Portrait AI Funestra": 3030, "Landscape AI Funestra": 4040,
        }
        topmost_calls: list[tuple] = []

        with _sequencer_stubs(start_core_session={"side_effect": _fake_core_in(MainMode.KINO)}, wait_for_window_by_title={"side_effect": lambda title, **kw: title_to_hwnd.get(title, 0)}, set_always_on_top={"side_effect": lambda h, v, **_kw: topmost_calls.append((h, v))}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert {h for h, on in topmost_calls if on} == {3030, 4040, 2525}


class _TrackingProgress:
    cancelled = False

    def __init__(self, log: list[str] | None = None) -> None:
        self.phases: list[str] = log if log is not None else []

    def advance(self, phase: str) -> None:
        self.phases.append(phase)

    def finish(self) -> None:
        pass

    def loading_screen_kept_up(self):
        return contextlib.nullcontext()


class _CancelOnAdvance:
    def __init__(self, cancel_on: int) -> None:
        self._cancel_on = cancel_on
        self.calls = 0

    @property
    def cancelled(self) -> bool:
        return self.calls >= self._cancel_on

    def advance(self, message: str) -> None:
        self.calls += 1
        if self.calls >= self._cancel_on:
            raise StartupCancelled()

    def finish(self) -> None:
        pass

    def loading_screen_kept_up(self):
        return contextlib.nullcontext()


class TestTheOrderInsideTheStartupPhases:
    def _sequence(self, cfg_factory, tmp_path, *, hide_windows=False,
                  overrides=None, extra_stubs=None) -> list[str]:
        cfg = load_config(cfg_factory(overrides or {}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)
        order: list[str] = []

        def note(name, then=None):
            def recorder(*args, **kwargs):
                order.append(name)
                return then(*args, **kwargs) if then is not None else 0
            return recorder

        stubs = {
            "screen_layout": {"side_effect": note("layout", fake_screen_layout)},
            "start_core_session": {"side_effect": note("core", _fake_core)},
            "launch_main_funestra": {"side_effect": note("main_funestra", _fake_main_funestra)},
            "launch_ui_companions": {"side_effect": note("companions", _fake_ui)},
        }
        stubs.update(extra_stubs or {})
        with _sequencer_stubs(**stubs), \
             patch("fun_time.windows_bridge_sequencer._maybe_launch_random_faves_browser",
                   side_effect=note("browser")), \
             patch("fun_time.windows_bridge_sequencer.write_flag_file",
                   side_effect=note("release")):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path,
                                 hide_windows=hide_windows)
        return order

    def test_the_layout_is_computed_before_any_funestra_launches(self, cfg_factory, tmp_path):
        order = self._sequence(cfg_factory, tmp_path)

        assert order.index("layout") < order.index("core")

    def test_the_main_funestras_slow_load_has_the_companions_launch_to_run_in(self, cfg_factory, tmp_path):
        order = self._sequence(cfg_factory, tmp_path)

        assert order.index("main_funestra") < order.index("companions")

    def test_the_browser_is_up_before_the_dashboard_that_opens_over_it(
            self, cfg_factory, tmp_path):
        order = self._sequence(cfg_factory, tmp_path)

        assert order.index("browser") < order.index("companions")

    def test_the_funestras_are_released_only_once_the_app_is_built(
            self, cfg_factory, tmp_path):
        order = self._sequence(cfg_factory, tmp_path)

        assert order[-1] == "release"
        assert order.index("companions") < order.index("release")

    def test_a_covered_startup_releases_nothing_at_all(self, cfg_factory, tmp_path):
        order = self._sequence(cfg_factory, tmp_path, hide_windows=True)

        assert "release" not in order


class TestRunStartupSequenceCancellation:
    def test_cancel_before_companions_reports_only_the_core_children(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        ui = MagicMock()

        with (
            _sequencer_stubs(launch_ui_companions=dict(ui)),
            pytest.raises(StartupCancelled) as excinfo,
        ):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path,
                progress=_CancelOnAdvance(cancel_on=2), hide_windows=True,
            )

        exc = excinfo.value
        assert set(exc.launched_pids) == {30, 40, MAIN_FUNESTRA_PID}
        assert exc.rfb_hwnd == 0
        ui.assert_not_called()

    def test_cancel_after_companions_reports_every_child_and_the_browser(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with (
            _sequencer_stubs(_maybe_launch_random_faves_browser={"return_value": 7777}, wait_for_window_by_title={"return_value": 88888}),
            pytest.raises(StartupCancelled) as excinfo,
        ):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path,
                progress=_CancelOnAdvance(cancel_on=5), hide_windows=True,
            )

        exc = excinfo.value
        assert set(exc.launched_pids) == {30, 40, MAIN_FUNESTRA_PID, 50, 70}
        assert exc.rfb_hwnd == 7777


class TestAFunestraThatDiesWhileTheRoomComesUp:
    def test_the_startup_ends_naming_the_funestra_and_what_it_said(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        def a_main_funestra_that_cannot_load_its_engine(m, **kwargs):
            with Path(kwargs["log_file"]).open("a", encoding="utf-8") as log:
                log.write("OSError: The engine (libmpv) could not be loaded. Looked in: "
                          "C:\\funestra\\vendor (no libmpv-2.dll in it)\n")
            return MAIN_FUNESTRA_PID

        with (
            _sequencer_stubs(
                launch_main_funestra={"side_effect": a_main_funestra_that_cannot_load_its_engine},
                is_process_alive={"side_effect": lambda pid: pid != MAIN_FUNESTRA_PID}),
            pytest.raises(FunestraDied) as died,
        ):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True)

        assert died.value.funestra.name == "the Main Funestra"
        assert "no libmpv-2.dll in it" in died.value.said

    def test_what_was_launched_comes_back_with_it_to_be_torn_down(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with (
            _sequencer_stubs(
                is_process_alive={"side_effect": lambda pid: pid != MAIN_FUNESTRA_PID}),
            pytest.raises(FunestraDied) as died,
        ):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True)

        assert set(died.value.launched_pids) >= {30, 40, MAIN_FUNESTRA_PID}

    def test_a_dead_funestra_is_not_waited_out_as_a_slow_one_is(self, tmp_path):
        gone = [LaunchedFunestra("the Main Funestra", MAIN_FUNESTRA_PID)]

        with (
            patch("fun_time.funestra_deaths.is_process_alive", return_value=False),
            sleeps_in(windows_bridge_sequencer),
            pytest.raises(FunestraDied),
        ):
            _wait_for_main_funestra_loaded(
                tmp_path / "never.txt", NullProgress(), timeout_s=0.3, funestras=gone)

    def test_an_app_whose_funestras_are_all_running_is_not_stopped(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with _sequencer_stubs():
            result = run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True)

        assert result.main_funestra_pid == MAIN_FUNESTRA_PID


class TestLoadingScreenStartup:
    def test_defers_positioning_under_the_overlay(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        title_to_hwnd = {"Portrait AI Funestra": 3030, "Landscape AI Funestra": 4040}
        move_calls: list[tuple] = []
        move_activates: list[bool] = []

        def track_move(hwnd, rect, **kw):
            move_calls.append((hwnd, *rect))
            move_activates.append(kw.get("activate", True))

        with _sequencer_stubs(wait_for_window_by_title={"side_effect": lambda title, **kw: title_to_hwnd.get(title, 88888)}, move_window={"side_effect": track_move}):
            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        positioned_hwnds = {hwnd for hwnd, x, y, w, h in move_calls}
        assert {3030, 4040} <= positioned_hwnds

        assert all(activate is False for activate in move_activates), \
            f"move_window must not activate in loading screen mode: {move_activates}"

    def test_resolves_every_window_by_title_never_by_a_launcher_pid(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        title_to_hwnd = {
            "Portrait AI Funestra": 3030,
            "Landscape AI Funestra": 4040,
            "Main Funestra": 2525,
            "Fun Time": 5050,
        }

        with _sequencer_stubs(wait_for_window_by_title={"side_effect": lambda title, **kw: title_to_hwnd.get(title, 0)}):
            result = run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

        assert not hasattr(windows_bridge_sequencer, "wait_for_window")
        assert not hasattr(windows_bridge_sequencer, "find_window_by_pid")
        assert result.hwnds_by_window == {
            "portrait": 3030, "landscape": 4040, "main_funestra": 2525,
            "dashboard": 5050, "rfb": 0,
        }, "the hosted app is still booting; the dispatch loop finds its windows when asked"

    def test_the_dashboard_is_found_hidden_under_the_loading_screen(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        def only_a_hidden_lookup_finds_the_dashboard(title, **kw):
            if title == "Fun Time":
                return 5050 if kw.get("include_hidden") else 0
            return 88888

        with _sequencer_stubs(wait_for_window_by_title={
                "side_effect": only_a_hidden_lookup_finds_the_dashboard}):
            result = run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True)

        assert result.hwnds_by_window["dashboard"] == 5050


class TestTheRoomIsBandedUnderTheLoadingScreen:
    HWNDS_BY_WINDOW: ClassVar[dict] = {"rfb": 11, "portrait": 22, "landscape": 33, "dashboard": 44,
                  "main_funestra": 55}
    LOADING_SCREEN = 999

    def _calls(self, **kwargs):
        calls: list[tuple[int, bool, int]] = []
        with patch("fun_time.windows_bridge_sequencer.set_always_on_top",
                   side_effect=lambda h, v, *, under=0: calls.append((h, v, under))):
            windows_bridge_sequencer.apply_topmost_bands(dict(self.HWNDS_BY_WINDOW), **kwargs)
        return calls

    def test_every_promotion_names_the_loading_screen_to_sit_under(self):
        promotions = [(h, under) for h, on, under in self._calls(beneath=self.LOADING_SCREEN) if on]

        assert promotions, "nothing was promoted, so this proves nothing"
        assert all(under == self.LOADING_SCREEN for _h, under in promotions), (
            "Windows puts a window made topmost at the very top, over the loading screen, "
            "until the loading screen takes the top back")

    def test_the_walk_still_promotes_in_role_order(self):
        banded = [h for h, on, _under in self._calls(beneath=self.LOADING_SCREEN) if on]
        plain = [h for h, on, _under in self._calls() if on]

        assert banded == plain

    def test_the_walk_touches_only_the_windows_it_bands(self):
        assert {h for h, _on, _under in self._calls(beneath=self.LOADING_SCREEN)} <= set(
            self.HWNDS_BY_WINDOW.values())

    def test_without_a_loading_screen_a_promotion_goes_to_the_top_of_the_band(self):
        assert all(under == 0 for _h, _on, under in self._calls())

    def test_an_app_opening_with_origenerator_in_the_browsers_place_keeps_the_browser_under_it(
            self):
        for beneath in (self.LOADING_SCREEN, 0):
            calls = self._calls(beneath=beneath, rfb_mode=RfbMode.ORIGENERATOR)
            assert [on for h, on, _under in calls if h == self.HWNDS_BY_WINDOW["rfb"]] == [False]


class TestPhase4Reveal:
    def _run_hidden(self, manifest_path, tmp_path, *, title_to_hwnd=None, topmost_calls=None,
                    mode="kino"):
        title_map = title_to_hwnd or {"Fun Time": 5050, "Main Funestra": 2525}
        # Both Funestras reporting frames: the loading screen waits for that before it
        # comes down (a satellite's window exists long before mpv has drawn
        # anything into it), so a run with no status files would hold it up.
        manifest = LaunchManifest.read(manifest_path)
        for funestra in FunTimeFunestra.SATELLITES:
            status = manifest.satellite(funestra).status_file
            status.parent.mkdir(parents=True, exist_ok=True)
            status.write_text("video=a.mp4\nposition_ms=250\n", encoding="utf-8")
        topmost_tracker = (lambda h, v, **_kw: topmost_calls.append((h, v))) if topmost_calls is not None else (lambda h, v, **_kw: None)

        with _sequencer_stubs(start_core_session={"side_effect": _fake_core_in(mode)}, wait_for_window_by_title={"side_effect": lambda title, **kw: title_map.get(title, 0)}, set_always_on_top={"side_effect": topmost_tracker}):
            return run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                hide_windows=True,
            )

    def test_every_funestra_is_still_held_when_the_phases_end(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = LaunchManifest.read(manifest_path)
        held = _pause_every_funestra(m)

        self._run_hidden(manifest_path, tmp_path)

        for flag in held:
            assert flag.read_text(encoding="utf-8").strip() == "1", flag.name

    def test_the_release_starts_the_funestras_the_mode_shows(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        m = LaunchManifest.read(manifest_path)
        _pause_every_funestra(m)

        release_the_funestras(m, "kino")

        assert Path(m.commands.main_funestra_paused_file).read_text(encoding="utf-8").strip() == "0"
        assert Path(m.commands.genau_paused_file).read_text(encoding="utf-8").strip() == "0"
        assert Path(m.commands.audio_paused_file).read_text(encoding="utf-8").strip() == "0"

    def test_nothing_is_promoted_topmost_while_the_loading_overlay_is_up(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        topmost_calls: list[tuple] = []
        self._run_hidden(
            manifest_path, tmp_path, topmost_calls=topmost_calls,
        )

        assert topmost_calls == []

class TestMainFunestraGatesTheReveal:
    def test_the_funestras_phase_covers_the_wait_for_main_funestra(self, cfg_factory, tmp_path):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        events: list[str] = []

        def track_wait(status_file, *_args, **_kwargs):
            events.append(f"wait-for-main_player:{status_file}")
            return True

        with _sequencer_stubs(wait_for_window_by_title={"return_value": 88888}, _wait_for_main_funestra_loaded={"side_effect": track_wait}):
            run_startup_sequence(
                manifest_path=manifest_path,
                state_dir=tmp_path,
                progress=_TrackingProgress(events),
                hide_windows=True,
            )

        assert events == [
            "services",
            "browser",
            "companions",
            "funestras",
            f"wait-for-main_player:{cfg.main_funestra_status_file}",
            "windows",
        ]

    def test_the_stale_status_is_read_for_the_resume_and_only_then_dropped(
        self, cfg_factory, tmp_path,
    ):
        cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        status_file = Path(cfg.main_funestra_status_file)
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text("video=last_session.mp4\n", encoding="utf-8")

        seen: dict = {}

        def capture_core(m, **kwargs):
            seen["resumed_onto"] = read_main_funestra_status(
                Path(m.commands.main_funestra_status_file)).video
            _write_result(kwargs["result_file"], CORE_PIDS)

        def capture_main_funestra(m, **kwargs):
            seen["stale_at_launch"] = Path(m.commands.main_funestra_status_file).exists()
            return _fake_main_funestra(m, **kwargs)

        with _sequencer_stubs(start_core_session={"side_effect": capture_core}, launch_main_funestra={"side_effect": capture_main_funestra}, wait_for_window_by_title={"return_value": 88888}):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert seen["resumed_onto"] == "last_session.mp4"
        assert seen["stale_at_launch"] is False, (
            "the wait would end at once on a video from a session that is over")

    def test_every_wait_for_the_funestras_and_their_windows_keeps_the_loading_screen_up(
        self, cfg_factory, tmp_path,
    ):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        progress = _KeptUpProgress()

        def waits(result):
            def wait(*_args, **_kwargs):
                progress.waits_heard.append(progress.kept_up)
                return result
            return wait

        with _sequencer_stubs(
            wait_for_window_by_title={"side_effect": waits(88888)},
            _wait_for_main_funestra_loaded={"side_effect": waits(True)},
            _wait_for_funestras_drawing={"side_effect": waits(True)},
        ):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path,
                progress=progress, hide_windows=True,
            )

        assert len(progress.waits_heard) >= 4
        assert all(progress.waits_heard), (
            "their worst cases add up past the loading screen's patience, "
            "which would take it down while Fun Time is still starting")


class _KeptUpProgress(_TrackingProgress):
    def __init__(self) -> None:
        super().__init__()
        self.kept_up = False
        self.waits_heard: list[bool] = []

    @contextlib.contextmanager
    def loading_screen_kept_up(self):
        self.kept_up = True
        try:
            yield
        finally:
            self.kept_up = False


FAKE_LAYOUT_CFG = LayoutConfig(
    primary_monitor=0,
    secondary_monitor=1,
    main_top_ratio=0.48,
    landscape_width_ratio=0.35,
)

MAIN_RECT = MonitorRect(x=0, y=0, width=2560, height=1392)


class TestWaitForNewChromeWindow:
    def _clock(self, monkeypatch):
        ticks = iter(range(1000))
        monkeypatch.setattr(
            windows_bridge_sequencer, "time",
            SimpleNamespace(monotonic=lambda: float(next(ticks)), sleep=lambda _s: None),
        )

    def test_the_window_that_appeared_is_the_one_handed_back(self, monkeypatch):
        self._clock(monkeypatch)
        snapshots = iter([{111, 222}, {111, 222}, {111, 222, 333}])
        with patch("fun_time.windows_bridge_sequencer.find_windows_by_class",
                   side_effect=lambda _cls: next(snapshots)):
            hwnd = windows_bridge_sequencer._wait_for_new_chrome_window(
                {111, 222}, timeout_ms=8000)

        assert hwnd == 333

    def test_gives_up_at_the_deadline_when_no_window_appears(self, monkeypatch):
        self._clock(monkeypatch)
        with patch("fun_time.windows_bridge_sequencer.find_windows_by_class",
                   return_value={111, 222}):
            hwnd = windows_bridge_sequencer._wait_for_new_chrome_window(
                {111, 222}, timeout_ms=3000)

        assert hwnd == 0


class TestMaybeLaunchRandomFavesBrowser:
    def _browser_settings(self, *, enabled: bool = True) -> RandomFavesBrowserSettings:
        return RandomFavesBrowserSettings(
            enabled=enabled,
            shortcut_path=r"C:\fake\shortcut.lnk",
            manifest_file=r"C:\fake\manifest.ini",
        )

    def _fake_plan(self) -> WindowLayoutPlan:
        return compute_window_layout(
            primary_monitor=MAIN_RECT,
            secondary_monitor=MonitorRect(x=2560, y=0, width=1440, height=3440),
            layout_config=FAKE_LAYOUT_CFG,
        )

    def test_skipped_when_disabled(self):
        m = self._browser_settings(enabled=False)
        plan = self._fake_plan()
        move_calls: list[tuple] = []

        with patch("fun_time.windows_bridge_sequencer.move_window",
                    side_effect=lambda *a, **kw: move_calls.append(a)):
            rfb_hwnd = _maybe_launch_random_faves_browser(m, plan, env=SessionEnvironment())

        assert move_calls == []
        assert rfb_hwnd == 0

    def test_launches_and_positions_browser(self):
        m = self._browser_settings()
        plan = self._fake_plan()
        browser_rect = plan.random_faves_browser

        launch_result = MagicMock(should_launch=True)

        with patch("fun_time.windows_bridge_sequencer.resolve_shortcut",
                   return_value=Shortcut("chrome.exe")), \
             patch("fun_time.windows_bridge_sequencer.find_windows_by_class", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_faves_browser", return_value=launch_result), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window") as mock_move:
            rfb_hwnd = _maybe_launch_random_faves_browser(m, plan, env=SessionEnvironment())

        mock_move.assert_called_once_with(55555, browser_rect, activate=True)
        assert rfb_hwnd == 55555

    def test_launches_the_urls_the_manifest_already_resolved(self):
        m = self._browser_settings()
        plan = self._fake_plan()

        launch_kwargs: dict = {}
        launch_result = MagicMock(should_launch=True)

        def capture_launch(*args, **kwargs):
            launch_kwargs.update(kwargs)
            return launch_result

        with patch("fun_time.windows_bridge_sequencer.resolve_shortcut",
                   return_value=Shortcut("chrome.exe")), \
             patch("fun_time.windows_bridge_sequencer.find_windows_by_class", return_value=set()), \
             patch("fun_time.windows_bridge_sequencer.launch_random_faves_browser", side_effect=capture_launch), \
             patch("fun_time.windows_bridge_sequencer._wait_for_new_chrome_window", return_value=55555), \
             patch("fun_time.windows_bridge_sequencer.move_window"):
            _maybe_launch_random_faves_browser(m, plan, env=SessionEnvironment())

        assert set(launch_kwargs) == {"shortcut"}
        assert launch_kwargs["shortcut"] == Shortcut("chrome.exe")


class TestResolveSatelliteHwnds:
    def test_no_satellite_is_taken_for_the_other_by_its_title(self):
        title_to_hwnd = {"Portrait AI Funestra": 1111, "Landscape AI Funestra": 2222}

        with patch(
            "fun_time.windows_bridge_sequencer.wait_for_window_by_title",
            side_effect=lambda title, **kw: title_to_hwnd.get(title, 0),
        ) as by_title:
            portrait, landscape = _resolve_satellite_hwnds()

        assert (portrait, landscape) == (1111, 2222), (
            "a title the two share crosses them: each is placed as the other")
        resolved = {call.args[0] for call in by_title.call_args_list}
        assert resolved == {"Portrait AI Funestra", "Landscape AI Funestra"}
        assert all(call.kwargs.get("exact") is True for call in by_title.call_args_list)


class TestWaitForFunestrasDrawing:
    def test_the_wait_ends_under_a_clock_that_never_moves(self, tmp_path):
        status_file = tmp_path / "portrait_status.txt"
        status_file.write_text("position_ms=0\n", encoding="utf-8")
        polls = int(SATELLITE_PLAY_TIMEOUT_S / _PLAY_POLL_S)
        slept: list[float] = []

        def sleep(seconds):
            slept.append(seconds)
            assert len(slept) <= polls, "still polling under a clock that never moves"

        with patch.object(windows_bridge_sequencer, "time",
                          SimpleNamespace(monotonic=lambda: 0.0, sleep=sleep)):
            assert _wait_for_funestras_drawing([status_file], NullProgress()) is False

        assert len(slept) == polls


class TestWaitForMainFunestraLoaded:
    def test_returns_once_main_funestra_reports_a_video(self, tmp_path):
        status_file = tmp_path / "main_player_status.txt"

        def main_funestra_finishes_loading(_seconds):
            status_file.write_text("video=clip.mp4\n", encoding="utf-8")

        with sleeps_in(windows_bridge_sequencer) as slept:
            slept.side_effect = main_funestra_finishes_loading
            assert _wait_for_main_funestra_loaded(status_file, NullProgress()) is True

    def test_a_status_caught_half_written_keeps_the_wait_going(self, tmp_path):
        status_file = tmp_path / "main_player_status.txt"
        status_file.write_text("", encoding="utf-8")

        with sleeps_in(windows_bridge_sequencer):
            assert _wait_for_main_funestra_loaded(
                status_file, NullProgress(), timeout_s=0.3,
            ) is False

    def test_a_main_funestra_that_never_loads_gives_the_desktop_up_rather_than_keep_it(self, tmp_path):
        with sleeps_in(windows_bridge_sequencer):
            assert _wait_for_main_funestra_loaded(
                tmp_path / "never.txt", NullProgress(), timeout_s=0.0,
            ) is False

    def test_esc_is_answered_on_every_poll_of_startups_longest_wait(self, tmp_path):
        class Cancelled:
            cancelled = True
            def advance(self, phase: str) -> None: pass
            def finish(self) -> None: pass

        with sleeps_in(windows_bridge_sequencer) as slept, pytest.raises(StartupCancelled):
            _wait_for_main_funestra_loaded(tmp_path / "never.txt", Cancelled())

        slept.assert_not_called()


@dataclass(frozen=True)
class Offer:
    created_at: int | None = None
    starting: bool = False
    showing_on: str | None = None


class TestOrigeneratorLaunch:
    def test_a_configured_origenerator_launches_with_the_layout(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
            "origenerator_python_exe": str(tmp_path / "py" / "python.exe"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return 91

        with _sequencer_stubs(launch_origenerator={"side_effect": capture}):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_pid == 91
        assert not result.origenerator_taken_over
        assert captured["origenerator_dir"] == str(tmp_path / "origenerator")
        assert captured["python_exe"] == str(tmp_path / "py" / "python.exe")
        assert captured["session"].layout_plan.random_faves_browser.width > 0
        assert str(captured["session"].command_file).endswith("origenerator_cmd.txt")
        assert Path(captured["session"].paused_file).read_text(encoding="utf-8") == "0"

    def test_the_hosted_app_is_handed_both_funestras(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        stale = cfg.satellite(FunTimeFunestra.PORTRAIT).origenerator_hud_file
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text('{"Funestra": "portrait"}', encoding="utf-8")
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs, stale_at_launch=stale.exists())
            return 91

        with _sequencer_stubs(launch_origenerator={"side_effect": capture}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        for funestra in FunTimeFunestra.SATELLITES:
            files = cfg.satellite(funestra)
            assert captured["session"].funestras[funestra.label] == HandedFunestra(
                playlist_file=files.playlist_file, cmd_file=files.cmd_file,
                status_file=files.status_file, hud_file=files.origenerator_hud_file)
        assert captured["stale_at_launch"] is False

    def test_an_origenerator_with_no_interpreter_named_runs_from_its_own_install(
            self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return 91

        with _sequencer_stubs(launch_origenerator={"side_effect": capture}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert Path(captured["python_exe"]) == (
            tmp_path / "origenerator" / ".venv" / "Scripts" / "python.exe")

    def test_a_named_interpreter_never_looks_up_the_install(self, cfg_factory, tmp_path):
        named = tmp_path / "py" / "python.exe"
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
            "origenerator_python_exe": str(named),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return 91

        with _sequencer_stubs(launch_origenerator={"side_effect": capture}), patch(
                "fun_time.hosted_origenerator.origenerator_interpreter") as install_lookup:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert Path(captured["python_exe"]) == named.resolve()
        install_lookup.assert_not_called()

    def test_without_a_configured_origenerator_nothing_launches(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)
        with _sequencer_stubs(launch_origenerator={}) as stubs:
            launch = stubs.launch_origenerator
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        launch.assert_not_called()
        assert result.origenerator_pid == 0

    def _checkout_with_an_open_app(self, cfg_factory, tmp_path, offer=None, *, hosting=None):
        offer = offer or Offer()
        checkout = tmp_path / "origenerator"
        cfg = load_config(cfg_factory({"paths": {"origenerator_dir": str(hosting or checkout)}}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        open_app = os.getpid()
        offered = handoff.Process(open_app, offer.created_at or process_started(open_app))
        (checkout / "state").mkdir(parents=True)
        (checkout / "state" / handoff.OFFER).write_text(
            f"{handoff.offer_of(offered, starting=offer.starting)}\n", encoding="utf-8")
        if offer.showing_on:
            (checkout / "state" / handoff.SHOWING).write_text(
                handoff.slideshow_mark_of(offered, offer.showing_on), encoding="utf-8")
        return cfg, manifest_path, checkout, open_app

    @contextlib.contextmanager
    def _answering(self):
        handed: dict = {}

        def answer(place, **kw):
            hand_over(place, **kw)
            handed.update(take_the_takeover(place)._asdict())

        with origenerator_handed_over(side_effect=answer):
            yield handed

    def test_an_origenerator_still_starting_is_taken_over_rather_than_launched(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path, Offer(starting=True))

        with _sequencer_stubs(launch_origenerator={}) as stubs, \
                self._answering() as handed:
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        stubs.launch_origenerator.assert_not_called()
        assert result.origenerator_pid == open_app
        assert handed["pid"] == open_app

    def test_a_session_hosting_a_worktree_takes_over_the_everyday_copy_open_on_its_library(
        self, cfg_factory, tmp_path
    ):
        worktree = a_worktree_of(tmp_path / "origenerator",
                                 tmp_path / "origenerator" / ".claude" / "worktrees" / "some-branch")
        _cfg, manifest_path, _everyday, open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path, hosting=worktree)

        with _sequencer_stubs(launch_origenerator={}) as stubs, \
                self._answering() as handed:
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        stubs.launch_origenerator.assert_not_called()
        assert result.origenerator_pid == open_app
        assert handed["pid"] == open_app

    def test_an_app_that_took_over_an_app_still_starting_has_to_hear_from_it(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path, Offer(starting=True))

        with _sequencer_stubs(launch_origenerator={}), self._answering():
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert not result.origenerator_already_open
        assert result.origenerator_taken_over

    def test_a_startup_canceled_after_taking_over_an_app_still_starting_leaves_it_off_the_kill_list(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path, Offer(starting=True))

        with _sequencer_stubs(launch_origenerator={}), self._answering(), \
             pytest.raises(StartupCancelled) as excinfo:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path,
                                 progress=_CancelOnAdvance(cancel_on=2))

        assert open_app not in excinfo.value.launched_pids
        assert excinfo.value.origenerator_taken_over

    def test_an_origenerator_already_open_is_taken_over_rather_than_launched(
        self, cfg_factory, tmp_path
    ):
        cfg, manifest_path, _checkout, open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path)

        with _sequencer_stubs(launch_origenerator={}) as stubs,                 self._answering() as handed:
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        stubs.launch_origenerator.assert_not_called()
        assert result.origenerator_pid == open_app
        assert handed["pid"] == open_app
        args = handed["args"]
        assert args[0] == "--fun-time"
        for flag, path in (("--command-file", cfg.origenerator_cmd_file),
                           ("--paused-file", cfg.origenerator_paused_file),
                           ("--status-file", cfg.origenerator_status_file)):
            assert args[args.index(flag) + 1] == str(path), flag
        assert Path(cfg.origenerator_paused_file).read_text(encoding="utf-8") == "0"

    def test_a_frozen_app_whose_offer_still_stands_is_replaced_by_our_own(
        self, cfg_factory, tmp_path
    ):
        cfg, manifest_path, _checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path)
        stale_status = Path(cfg.origenerator_status_file)
        stale_status.write_text("portrait_active=1\n", encoding="utf-8")

        with _sequencer_stubs(launch_origenerator={"return_value": 91}) as stubs, \
             patch("fun_time.hosted_origenerator.ANSWER_BUDGET_S", 0.05):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert not stale_status.exists()
        assert stubs.launch_origenerator.called
        assert result.origenerator_pid == 91
        assert not result.origenerator_taken_over

    def test_a_session_with_no_window_to_take_over_still_claims_the_osr2(
        self, cfg_factory, tmp_path
    ):
        checkout = tmp_path / "origenerator"
        cfg = load_config(cfg_factory({"paths": {"origenerator_dir": str(checkout)}}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        with _sequencer_stubs(launch_origenerator={}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert handoff.a_session_claims_the_osr2(checkout / "state"), (
            "a window the takeover missed would keep its own switch and drive the OSR2 too")

    def test_a_session_with_no_checkout_claims_nothing(self, cfg_factory, tmp_path):
        _cfg, manifest_path = _make_manifest(cfg_factory, tmp_path)

        with _sequencer_stubs(launch_origenerator={}):
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert not list(tmp_path.glob(f"**/{handoff.SESSION}"))

    def test_an_app_taken_over_before_a_crossing_is_adopted_as_one_to_hand_back(
        self, cfg_factory, tmp_path
    ):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator")}}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        open_app = os.getpid()
        keep_the_origenerator(Path(cfg.origenerator_status_file).parent, pid=open_app,
                              created_at=process_started(open_app), taken_over=True)

        with _sequencer_stubs(launch_origenerator={}) as stubs, \
             pytest.raises(StartupCancelled) as excinfo:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path,
                                 progress=_CancelOnAdvance(cancel_on=2))

        stubs.launch_origenerator.assert_not_called()
        assert open_app not in excinfo.value.launched_pids
        assert excinfo.value.origenerator_taken_over

    def test_a_takeover_is_carried_out_of_startup_as_an_app_to_hand_back(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path)

        with _sequencer_stubs(launch_origenerator={}), self._answering():
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_taken_over

    def test_a_startup_canceled_after_a_takeover_leaves_that_app_off_the_kill_list(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path)

        with _sequencer_stubs(launch_origenerator={}), self._answering(), \
             pytest.raises(StartupCancelled) as excinfo:
            run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path,
                                 progress=_CancelOnAdvance(cancel_on=2))

        assert open_app not in excinfo.value.launched_pids
        assert excinfo.value.origenerator_taken_over

    def test_an_app_that_took_an_open_app_over_knows_it_was_already_open(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path)

        with _sequencer_stubs(launch_origenerator={}), self._answering():
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_already_open

    def test_an_app_that_took_over_an_app_showing_a_slideshow_carries_that_out_of_startup(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path, Offer(showing_on="landscape"))

        with _sequencer_stubs(launch_origenerator={}), self._answering():
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_showing_a_slideshow is True

    def test_an_app_that_took_over_an_app_showing_nothing_carries_no_slideshow(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path, _checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path)

        with _sequencer_stubs(launch_origenerator={}), self._answering():
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_showing_a_slideshow is False

    def test_an_app_that_adopted_the_app_a_crossing_kept_knows_it_was_already_open(
        self, cfg_factory, tmp_path
    ):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator")}}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        kept_app = os.getpid()
        keep_the_origenerator(Path(cfg.origenerator_status_file).parent, pid=kept_app,
                              created_at=process_started(kept_app))

        with _sequencer_stubs(launch_origenerator={}):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_pid == kept_app
        assert result.origenerator_already_open

    def test_an_app_that_launched_its_app_has_to_hear_from_it(self, cfg_factory, tmp_path):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator")}}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )

        with _sequencer_stubs(launch_origenerator={"side_effect": _fake_origenerator}):
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        assert result.origenerator_pid == ORIGENERATOR_PID
        assert not result.origenerator_already_open

    def test_an_offer_left_by_an_app_since_closed_launches_one(self, cfg_factory, tmp_path):
        _cfg, manifest_path, checkout, _open_app = self._checkout_with_an_open_app(
            cfg_factory, tmp_path, Offer(created_at=1))

        with _sequencer_stubs(launch_origenerator={"side_effect": _fake_origenerator}) as stubs:
            result = run_startup_sequence(manifest_path=manifest_path, state_dir=tmp_path)

        stubs.launch_origenerator.assert_called_once()
        assert result.origenerator_pid == ORIGENERATOR_PID
        assert not handoff.a_takeover_waits_in(checkout / "state")


class TestOrigeneratorDoesNotHoldTheRoomUp:
    def _hosted(self, cfg_factory, tmp_path, *, left_in_origenerator: bool = False):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
        }}))
        manifest_path = write_windows_bridge_manifest(
            cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        if left_in_origenerator:
            write_shared_state(shared_state_path(tmp_path), BridgeState(
                portrait=SatelliteState(mode=SatellitesMode.ORIGENERATOR),
                rfb_mode=RfbMode.ORIGENERATOR))
        return cfg, manifest_path

    def test_every_session_opens_its_satellites_in_kino_mode(
        self, cfg_factory, tmp_path
    ):
        cfg, manifest_path = self._hosted(cfg_factory, tmp_path, left_in_origenerator=True)

        with _sequencer_stubs(launch_origenerator={"side_effect": _fake_origenerator}):
            result = run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert not [window_name for window_name in result.hwnds_by_window if window_name.startswith("origenerator")]
        assert (cfg.paths.state_dir / "origenerator_cmd.txt").read_text(encoding="utf-8") == "", (
            "no app is up yet to fill the regions; the dispatch loop opens the mode once it answers")

    def test_the_sequencer_can_no_longer_reach_the_hosted_window_at_all(self):
        assert not hasattr(windows_bridge_sequencer, "find_window_for_process")
        assert not hasattr(windows_bridge_sequencer, "restore_window")

    def test_the_hosted_apps_long_boot_is_started_first_of_everything(
        self, cfg_factory, tmp_path
    ):
        _cfg, manifest_path = self._hosted(cfg_factory, tmp_path)
        order: list[str] = []

        def note(name, answer):
            def stub(*args, **kwargs):
                order.append(name)
                return answer(*args, **kwargs) if callable(answer) else answer
            return stub

        with _sequencer_stubs(
            launch_origenerator={"side_effect": note("origenerator", _fake_origenerator)},
            start_core_session={"side_effect": note("satellites", _fake_core)},
        ):
            result = run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert order[0] == "origenerator"
        assert result.origenerator_pid == ORIGENERATOR_PID

    def test_the_bar_no_longer_carries_a_phase_for_it(self, cfg_factory, tmp_path):
        _cfg, manifest_path = self._hosted(cfg_factory, tmp_path)
        progress = _TrackingProgress()

        with _sequencer_stubs(launch_origenerator={"side_effect": _fake_origenerator}):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path,
                progress=progress, hide_windows=True,
            )

        assert "origenerator" not in progress.phases, (
            "its window lands 10 to 30 s after launch, against 5 to 8 s for everything else")

    def test_a_stranded_verb_is_cleared_before_the_hosted_app_can_read_it(
        self, cfg_factory, tmp_path
    ):
        cfg, manifest_path = self._hosted(cfg_factory, tmp_path)
        (cfg.paths.state_dir / "origenerator_cmd.txt").write_text(
            "PORTRAIT_NEXT\n", encoding="utf-8")

        with _sequencer_stubs(launch_origenerator={"side_effect": _fake_origenerator}):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert (cfg.paths.state_dir / "origenerator_cmd.txt").read_text(encoding="utf-8") == ""

    def test_last_sessions_status_cannot_pass_for_this_sessions_app(
        self, cfg_factory, tmp_path
    ):
        cfg, manifest_path = self._hosted(cfg_factory, tmp_path)
        status_file = Path(cfg.origenerator_status_file)
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text("portrait_active=1\nlandscape_active=1\n", encoding="utf-8")
        seen: dict = {}

        def capture(**kwargs):
            seen["stale_at_launch"] = Path(kwargs["session"].status_file).exists()
            return _fake_origenerator(**kwargs)

        with _sequencer_stubs(launch_origenerator={"side_effect": capture}):
            run_startup_sequence(
                manifest_path=manifest_path, state_dir=tmp_path, hide_windows=True,
            )

        assert seen["stale_at_launch"] is False
