from __future__ import annotations

import ast
import configparser
from contextlib import ExitStack, contextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app_support import kino_folder
from app_support.win32 import stamp_pinned_shortcuts

from fun_time import checkout_overrides, orchestrator
from fun_time.config import load_config
from fun_time.funestra_deaths import NO_ORIGENERATOR_SET_UP
from fun_time.manifest import (
    WINDOWS_BRIDGE_MANIFEST_FILENAME,
    build_windows_bridge_manifest,
    write_windows_bridge_manifest,
)
from fun_time.orchestrator import (
    build_parser,
    main,
    no_origenerator_set_up,
    require_dir,
    require_file,
    run_windows_bridge,
    startup_marker_path,
    tell_the_hidden_launcher_startup_resolved,
    validate_config,
)
from fun_time.project_paths import PROJECT_DIR, SIBLING_CHECKOUTS_OVERRIDE_NAME
from fun_time.session_environment import SessionEnvironment
from fun_time.session_handoff import VR, request_handoff
from fun_time.windows_bridge_orchestrator import HotkeyScriptLaunch, SessionOver
from tests.launcher_python import run_python_the_way_a_launcher_does
from tests.made_up_genau import point_the_shared_settings_at

REPO_DIR = Path(__file__).resolve().parent.parent


def _a_loading_screen() -> MagicMock:
    return MagicMock(spec=["progress", "take_it_down"])


@contextmanager
def _a_launch_with_the_machine_held_off(**overrides):
    steps_that_run_for_real_unless_held_here = {
        "configure_logging": MagicMock(return_value=MagicMock()),
        "install_exception_logging": MagicMock(),
        "claim_the_session": MagicMock(return_value=42),
        "ensure_runtime_files": MagicMock(),
        "validate_config": MagicMock(),
        "open_event_log": MagicMock(),
        "open_the_loading_screen": MagicMock(return_value=_a_loading_screen()),
        "stamp_the_users_pin_outside_every_checkout": MagicMock(),
        "describe_the_session_on_the_taskbar": MagicMock(),
        "engine_missing_abort": MagicMock(return_value=False),
        "no_origenerator_set_up": MagicMock(return_value=False),
        "prepare_orchestrator_launcher": MagicMock(),
        "wake_the_headset": MagicMock(),
        "run_windows_bridge": MagicMock(return_value=SessionOver(0)),
        "show_funestra_died_alert": MagicMock(),
        "put_the_headset_to_sleep": MagicMock(),
        **overrides,
    }
    with ExitStack() as stack:
        for name, mock in steps_that_run_for_real_unless_held_here.items():
            stack.enter_context(patch(f"fun_time.orchestrator.{name}", mock))
        yield SimpleNamespace(**steps_that_run_for_real_unless_held_here)


class TestBuildParser:
    def test_defaults(self):
        args = build_parser().parse_args([])
        assert args.config is None
        assert args.check is False

    def test_check_flag(self):
        args = build_parser().parse_args(["--check"])
        assert args.check is True

    def test_config_argument(self):
        args = build_parser().parse_args(["--config", "/path/config.json"])
        assert args.config == "/path/config.json"


class TestRequireFile:
    def test_passes_for_existing_file(self, tmp_path: Path):
        f = tmp_path / "exists.txt"
        f.touch()
        require_file(f)

    def test_raises_for_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Missing file"):
            require_file(tmp_path / "missing.exe")

    def test_raises_for_directory(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            require_file(tmp_path)


class TestRequireDir:
    def test_passes_for_existing_dir(self, tmp_path: Path):
        d = tmp_path / "mydir"
        d.mkdir()
        require_dir(d)

    def test_raises_for_missing_dir(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Missing directory"):
            require_dir(tmp_path / "nope")

    def test_raises_for_file(self, tmp_path: Path):
        f = tmp_path / "notadir.txt"
        f.touch()
        with pytest.raises(FileNotFoundError):
            require_dir(f)


class TestControllerManifest:
    def test_every_section_the_launch_needs_is_in_the_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert isinstance(result, dict)

    def test_layout_monitors_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["layout"]["primary_monitor"] == "1"
        assert result["layout"]["secondary_monitor"] == "2"

    def test_runtime_section_includes_config_path(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["runtime"]["config_path"] == str(cfg.config_path)

    def test_satellite_module_in_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["satellite_module"] == "satellite"

    def test_each_satellites_section_carries_its_own_files(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        manifest = build_windows_bridge_manifest(cfg)
        state = cfg.paths.state_dir
        for satellite in ("portrait", "landscape"):
            assert manifest[satellite]["cmd_file"] == str(state / f"{satellite}_cmd.txt")
            assert manifest[satellite]["paused_file"] == str(state / f"{satellite}_paused.txt")
            assert manifest[satellite]["status_file"] == str(state / f"{satellite}_status.txt")
            assert manifest[satellite]["playlist_file"] == str(state / f"{satellite}_playlist.tsv")

    def test_a_session_whose_state_folder_moved_is_still_told_the_brokers_own(
        self, tmp_path: Path, cfg_factory,
    ):
        broker_state = tmp_path / "primary_state"
        path = cfg_factory({"paths": {"broker_state_dir": str(broker_state)}})
        config = load_config(path)
        commands = build_windows_bridge_manifest(config)["commands"]

        assert config.paths.state_dir != broker_state
        assert commands["broker_state_dir"] == str(broker_state)
        assert commands["broker_cmd_file"] == str(broker_state / "broker_cmd.txt")
        assert commands["broker_mode_file"] == str(broker_state / "broker_mode.txt")

    def test_main_funestra_library_dirs_joined_with_pipe(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"main_player_library_dirs": [
            str(tmp_path / "main_player_library"),
            str(extra),
        ]}})
        cfg = load_config(path)
        manifest = build_windows_bridge_manifest(cfg)
        joined = manifest["media"]["main_funestra_library_sources"]
        assert str(tmp_path / "main_player_library") in joined
        assert str(extra) in joined
        assert "|" in joined

    def test_each_satellite_plays_kinos_folder_of_its_own_shape(self, cfg_path: Path):
        manifest = build_windows_bridge_manifest(load_config(cfg_path))
        kino = kino_folder.upscales_in(kino_folder.kino_dir())

        assert (manifest["portrait"]["sources"], manifest["landscape"]["sources"]) == (
            str(kino / "portrait"), str(kino / "landscape"))

    def test_audio_companion_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["audio_module"] == "fun_time.audio_companion_app"

    def test_dashboard_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["modules"]["dashboard_module"] == "fun_time.dashboard_app"

    def test_dashboard_enabled_defaults_true(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["dashboard"]["enabled"] == "1"

    def test_dashboard_enabled_can_be_disabled_for_integration(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg, dashboard_enabled=False)
        assert result["dashboard"]["enabled"] == "0"

    def test_the_environment_alone_no_longer_disables_the_dashboard(
            self, cfg_path: Path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_DISABLE_DASHBOARD", "1")
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["dashboard"]["enabled"] == "1"

    def test_media_actions_module_removed_from_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "media_actions_module" not in result["modules"]

    def test_windows_bridge_lock_module_name_included(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "windows_bridge_lock_module" not in result["modules"]

    def test_removed_modules_are_not_in_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert "windows_bridge_runtime_flow_module" not in result["modules"]

    def test_dead_app_modules_absent_from_manifest(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        for dead_key in (
            "windows_bridge_window_layout_module",
            "windows_bridge_random_faves_browser_module",
            "windows_bridge_startup_module",
            "windows_bridge_dashboard_bridge_module",
        ):
            assert dead_key not in result["modules"]

    def test_random_faves_browser_paths_included(self, cfg_factory):
        cfg = load_config(cfg_factory({"random_faves_browser": {"enabled": True, "profile_name": "Jane Doe"}}))
        result = build_windows_bridge_manifest(cfg)
        assert result["random_faves_browser"]["enabled"] == "1"
        assert result["random_faves_browser"]["shortcut_path"] == str(cfg.random_faves_browser.shortcut_path)
        assert result["random_faves_browser"]["manifest_file"] == str(cfg.random_faves_browser_manifest_file)

    def test_regen_section_included(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"regen": {
            "media_root": str(tmp_path / "media"),
            "metadata_root": str(tmp_path / "meta"),
        }})
        cfg = load_config(path)
        result = build_windows_bridge_manifest(cfg)
        assert result["regen"]["generate_video_url"] == "https://example.com/video"
        assert result["regen"]["generate_image_url"] == "https://example.com/create"
        assert result["regen"]["media_root"] == str(tmp_path / "media")
        assert result["regen"]["metadata_root"] == str(tmp_path / "meta")

    def test_regen_roots_blank_when_unset(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        result = build_windows_bridge_manifest(cfg)
        assert result["regen"]["media_root"] == ""
        assert result["regen"]["metadata_root"] == ""

    def test_write_windows_bridge_manifest_writes_expected_ini(self, cfg_factory, tmp_path: Path):
        cfg = load_config(cfg_factory({"random_faves_browser": {"enabled": True, "profile_name": "Jane Doe"}}))
        manifest_path = write_windows_bridge_manifest(cfg, tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)

        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(manifest_path, encoding="utf-8")

        assert manifest_path.name == WINDOWS_BRIDGE_MANIFEST_FILENAME
        assert parser["runtime"]["config_path"] == str(cfg.config_path)
        assert parser["modules"]["satellite_module"] == "satellite"
        assert parser["portrait"]["playlist_file"] == str(cfg.paths.state_dir / "portrait_playlist.tsv")
        assert parser["modules"]["audio_module"] == "fun_time.audio_companion_app"
        assert parser["modules"]["dashboard_module"] == "fun_time.dashboard_app"
        assert parser["commands"]["genau_paused_file"] == str(cfg.genau_paused_file)
        assert parser["commands"]["audio_paused_file"] == str(cfg.audio_paused_file)
        assert parser["commands"]["dashboard_state_file"] == str(cfg.paths.state_dir / "dashboard_state.ini")
        assert parser["commands"]["dashboard_cmd_file"] == str(cfg.paths.state_dir / "dashboard_cmd.txt")
        assert parser["random_faves_browser"]["enabled"] == "1"
        assert parser["random_faves_browser"]["manifest_file"] == str(cfg.random_faves_browser_manifest_file)


class TestValidateConfig:
    def _config_stubbing_only_what_lives_under_tmp_path(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        for p in (cfg.paths.ahk_exe, cfg.paths.python_exe):
            p.touch()
        return cfg

    def test_ahk_and_python_are_the_only_programs_a_launch_demands(self, cfg_path: Path):
        cfg = self._config_stubbing_only_what_lives_under_tmp_path(cfg_path)

        validate_config(cfg)

    def test_names_genaus_folder_when_nothing_in_it_is_laid_out_as_flicks_are(
            self, cfg_path: Path, tmp_path: Path, monkeypatch):
        genau = tmp_path / "bare_genau"
        genau.mkdir()
        point_the_shared_settings_at(monkeypatch, genau)
        cfg = self._config_stubbing_only_what_lives_under_tmp_path(cfg_path)

        with pytest.raises(FileNotFoundError, match="bare_genau"):
            validate_config(cfg)

    def test_names_genaus_audio_folder_when_it_is_missing(
            self, cfg_path: Path, genaus_folder: Path, tmp_path: Path, monkeypatch):
        point_the_shared_settings_at(monkeypatch, genaus_folder, tmp_path / "no_such_audio")
        cfg = self._config_stubbing_only_what_lives_under_tmp_path(cfg_path)

        with pytest.raises(FileNotFoundError, match="no_such_audio"):
            validate_config(cfg)

    def test_raises_when_random_faves_browser_shortcut_missing_if_enabled(self, cfg_factory: Path):
        cfg = load_config(
            cfg_factory(
                {
                    "random_faves_browser": {
                        "enabled": True,
                        "profile_name": "Jane Doe",
                        "shortcut_path": "missing_chrome.lnk",
                    }
                }
            )
        )
        with pytest.raises(FileNotFoundError):
            validate_config(cfg)


class TestTheEventLog:
    def test_the_log_is_emptied_before_any_tail_of_it_can_start(self, cfg_path: Path):
        order: list[str] = []

        def session(*_args, **_kwargs) -> SessionOver:
            order.append("session")
            return SessionOver(0)

        with _a_launch_with_the_machine_held_off(
            open_event_log=MagicMock(side_effect=lambda *_: order.append("emptied")),
            run_windows_bridge=MagicMock(side_effect=session),
        ):
            main(["--config", str(cfg_path)])

        assert order == ["emptied", "session"]


class TestBrokerHelpers:
    def test_the_broker_is_left_to_the_one_policy_inside_the_launch(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off() as launch:
            result = main(["--config", str(cfg_path)])

        assert result == 0
        assert not hasattr(orchestrator, "ensure_broker_running")
        launch.run_windows_bridge.assert_called_once()


class TestRunController:
    def _the_session_run_under(self, cfg, loading_screen):
        with patch("fun_time.orchestrator.write_windows_bridge_manifest", return_value=cfg.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME) as writer, \
             patch("fun_time.windows_bridge_orchestrator.run_session",
                   return_value=SessionOver(0)) as bridge:
            result = run_windows_bridge(cfg, MagicMock(), SessionEnvironment(), loading_screen)
        return result, writer, bridge

    def test_the_session_runs_under_the_loading_screen_already_up_never_its_own(self, cfg_path: Path):
        the_one_the_launch_raised = _a_loading_screen()

        _, _, bridge = self._the_session_run_under(load_config(cfg_path), the_one_the_launch_raised)

        assert bridge.call_args.kwargs["loading_screen"] is the_one_the_launch_raised

    def test_uses_manifest_path_for_bridge_launch(self, cfg_path: Path):
        cfg = load_config(cfg_path)

        result, writer, bridge = self._the_session_run_under(cfg, _a_loading_screen())

        assert result == SessionOver(0)
        writer.assert_called_once_with(cfg, dashboard_enabled=True)
        bridge.assert_called_once()
        call_kwargs = bridge.call_args.kwargs
        assert call_kwargs["manifest_path"] == cfg.paths.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME
        assert call_kwargs["hotkeys"] == HotkeyScriptLaunch(
            ahk_exe=str(cfg.paths.ahk_exe),
            script=str(cfg.project_dir / "windows_bridge_hotkeys.ahk"),
            project_dir=cfg.project_dir)
        assert call_kwargs["state_dir"] == cfg.paths.state_dir
        assert call_kwargs["env"] == SessionEnvironment()


class TestTheProcessEdgeReadsTheSwitchesOnce:
    def test_main_hands_the_session_what_the_environment_said(self, cfg_path: Path, monkeypatch):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
        monkeypatch.setenv("FUN_TIME_DISABLE_DASHBOARD", "1")

        with _a_launch_with_the_machine_held_off() as launch:
            main(["--config", str(cfg_path)])

        assert launch.run_windows_bridge.call_args.args[2] == SessionEnvironment(
            integration=True, show_overlays=False, dashboard_enabled=False)

    def test_the_headset_wake_is_told_whether_this_is_a_test_run(
        self, cfg_path: Path, monkeypatch,
    ):
        monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")

        with _a_launch_with_the_machine_held_off() as launch:
            main(["--config", str(cfg_path)])

        launch.wake_the_headset.assert_called_once_with(
            load_config(cfg_path).paths.state_dir, integration=True)


class TestTheCheckoutsItsOwnCheckoutNames:
    def test_a_config_an_older_production_checkout_wrote_still_runs_this_checkouts_siblings(
            self, cfg_path: Path, tmp_path: Path, monkeypatch):
        checkout = tmp_path / "funestra_worktree"
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / SIBLING_CHECKOUTS_OVERRIDE_NAME).write_text(
            f"{checkout}\n", encoding="utf-8")
        monkeypatch.setattr(checkout_overrides, "PROJECT_DIR", tmp_path)

        with _a_launch_with_the_machine_held_off() as launch:
            main(["--config", str(cfg_path)])

        assert launch.open_the_loading_screen.call_args.kwargs["sibling_checkouts"] == str(checkout)
        assert launch.run_windows_bridge.call_args.args[0].paths.sibling_checkouts == (checkout,)


class TestTheWayBackOffersNoEsc:
    def test_a_launch_started_as_a_way_back_is_handed_no_esc(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off() as launch:
            main(["--config", str(cfg_path), "--no-cancel"])

        assert launch.open_the_loading_screen.call_args.kwargs["cancelable"] is False


class TestMainCheckFlag:
    def test_the_check_flag_reports_success_without_starting_the_bridge(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off() as launch:
            result = main(["--config", str(cfg_path), "--check"])

        assert result == 0
        launch.run_windows_bridge.assert_not_called()

    def test_a_check_raises_no_loading_screen_and_stamps_no_pin(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off() as launch:
            main(["--config", str(cfg_path), "--check"])

        launch.open_the_loading_screen.assert_not_called()
        launch.stamp_the_users_pin_outside_every_checkout.assert_not_called()


class TestTheCrossingIntoTheOtherSession:
    def _main(self, cfg_path: Path, *, during_session=lambda: None):
        run_bridge = MagicMock(side_effect=lambda *_a, **_k: (during_session(), SessionOver(0))[1])
        with _a_launch_with_the_machine_held_off(run_windows_bridge=run_bridge) as launch, \
             patch("fun_time.session_handoff.subprocess.Popen") as popen:
            return main(["--config", str(cfg_path)]), popen, launch

    def test_a_session_that_asked_to_cross_spawns_the_relay_on_its_way_out(
        self, cfg_path: Path,
    ):
        config = load_config(cfg_path)

        code, popen, _launch = self._main(
            cfg_path,
            during_session=lambda: request_handoff(config.paths.state_dir, VR),
        )

        assert code == 0
        assert popen.call_args.args[0][:4] == [
            str(config.paths.python_exe), "-m", "fun_time.session_handoff", "--target",
        ]

    def test_an_ordinary_quit_spawns_nothing(self, cfg_path: Path):
        _code, popen, _launch = self._main(cfg_path)

        popen.assert_not_called()

    def test_a_crashs_leftover_request_never_sends_a_later_quit_into_the_headset(
        self, cfg_path: Path,
    ):
        config = load_config(cfg_path)
        request_handoff(config.paths.state_dir, VR)

        _code, popen, _launch = self._main(cfg_path)

        popen.assert_not_called()

    def test_a_session_that_asked_to_cross_leaves_the_headset_awake(self, cfg_path: Path):
        config = load_config(cfg_path)

        _code, _popen, launch = self._main(
            cfg_path,
            during_session=lambda: request_handoff(config.paths.state_dir, VR),
        )

        launch.put_the_headset_to_sleep.assert_called_once_with(
            config.paths.state_dir, crossing=True)

    def test_an_ordinary_quit_puts_the_headset_back_to_sleep(self, cfg_path: Path):
        _code, _popen, launch = self._main(cfg_path)

        launch.put_the_headset_to_sleep.assert_called_once_with(
            load_config(cfg_path).paths.state_dir, crossing=False)


class TestTheHeadsetIsAsleepBeforeTheRoomSaysWhyItClosed:
    def _closing_order(self, cfg_path: Path, *, crossing: bool) -> tuple[int, list[str]]:
        config = load_config(cfg_path)
        order: list[str] = []

        def a_session_that_ended_badly(*_args):
            if crossing:
                request_handoff(config.paths.state_dir, VR)
            return SessionOver(1, why="a part closed itself")

        with _a_launch_with_the_machine_held_off(
            run_windows_bridge=MagicMock(side_effect=a_session_that_ended_badly),
            put_the_headset_to_sleep=MagicMock(side_effect=lambda *_a, **_k: order.append("asleep")),
            show_funestra_died_alert=MagicMock(side_effect=order.append),
        ), patch("fun_time.session_handoff.subprocess.Popen",
                 side_effect=lambda *_a, **_k: order.append("relay")):
            code = main(["--config", str(cfg_path)])
        return code, order

    def test_the_headset_sleeps_before_the_words_go_up(self, cfg_path: Path):
        code, order = self._closing_order(cfg_path, crossing=False)

        assert order == ["asleep", "a part closed itself"]
        assert code == 1

    def test_a_crossing_starts_only_once_the_words_are_answered(self, cfg_path: Path):
        _code, order = self._closing_order(cfg_path, crossing=True)

        assert order[-2:] == ["a part closed itself", "relay"]

    def test_the_relay_that_waits_on_this_mutex_is_spawned_last(self, cfg_path: Path):
        _code, order = self._closing_order(cfg_path, crossing=True)

        assert order == ["asleep", "a part closed itself", "relay"]

    def test_an_error_that_ends_fun_time_puts_the_headset_to_sleep_even_mid_crossing(
        self, cfg_path: Path,
    ):
        config = load_config(cfg_path)

        def an_error_of_its_own(*_args):
            request_handoff(config.paths.state_dir, VR)
            raise RuntimeError("an error of its own")

        with _a_launch_with_the_machine_held_off(run_windows_bridge=MagicMock(side_effect=an_error_of_its_own)) as launch, \
             pytest.raises(RuntimeError, match="an error of its own"):
            main(["--config", str(cfg_path)])

        launch.put_the_headset_to_sleep.assert_called_once_with(
            config.paths.state_dir, crossing=False)


class TestMainDescribesTheSessionsTaskbarButton:
    def test_a_session_describes_the_button_of_the_checkout_it_runs_from(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off() as launch:
            main(["--config", str(cfg_path)])

        launch.describe_the_session_on_the_taskbar.assert_called_once_with(PROJECT_DIR)


class TestMainStampsOnlyTheMachinesOwnShortcut:
    def _main(self, cfg_path: Path, stamp):
        with _a_launch_with_the_machine_held_off(stamp_the_users_pin_outside_every_checkout=stamp):
            return main(["--config", str(cfg_path)])

    def test_a_session_on_another_config_leaves_the_pin_alone(self, cfg_path: Path):
        stamp = MagicMock()

        self._main(cfg_path, stamp)

        stamp.assert_not_called()

    def test_the_installed_app_still_stamps_its_own_pin(self, cfg_path: Path):
        stamp = MagicMock()

        with patch("fun_time.orchestrator.DEFAULT_CONFIG_PATH", Path(cfg_path)):
            self._main(cfg_path, stamp)

        stamp.assert_called_once_with()

    def test_a_retired_fun_time_vr_pin_is_not_stamped_as_fun_times(
            self, tmp_path: Path, monkeypatch):
        for name in ("Fun Time.lnk", "Fun Time VR.lnk"):
            (tmp_path / name).write_bytes(b"")
        stamped: list[str] = []
        monkeypatch.setattr(orchestrator, "stamp_pinned_shortcuts", partial(
            stamp_pinned_shortcuts, pins=tmp_path,
            stamp=lambda path, _app_id: stamped.append(Path(path).name)))

        orchestrator.stamp_the_users_pin_outside_every_checkout()

        assert stamped == ["Fun Time.lnk"]


class TestOrchestratorSingleInstance:
    def test_a_second_launch_raises_no_loading_screen_over_its_already_running_message(self, cfg_path: Path):
        show_msg = MagicMock()
        with _a_launch_with_the_machine_held_off(claim_the_session=MagicMock(return_value=None),
                       show_already_running_message=show_msg) as launch:
            result = main(["--config", str(cfg_path)])

        assert result == 1
        show_msg.assert_called_once()
        assert "already running" in show_msg.call_args[0][0]
        launch.run_windows_bridge.assert_not_called()
        launch.open_the_loading_screen.assert_not_called()


class TestTheLoadingScreenGoesUpFirst:
    def _order_of(self, cfg_path: Path) -> list[str]:
        order: list[str] = []
        loading_screen = _a_loading_screen()
        with _a_launch_with_the_machine_held_off(
            open_the_loading_screen=MagicMock(side_effect=lambda *_a, **_k: order.append("loading_screen") or loading_screen),
            engine_missing_abort=MagicMock(
                side_effect=lambda *_a, **_k: order.append("engine") or False),
            run_windows_bridge=MagicMock(
                side_effect=lambda *_a: order.append("session") or SessionOver(0)),
        ):
            main(["--config", str(cfg_path)])
        return order

    def test_the_monitors_are_covered_before_the_engine_is_checked(self, cfg_path: Path):
        assert self._order_of(cfg_path)[:2] == ["loading_screen", "engine"]

    def test_the_session_is_the_last_thing_under_it(self, cfg_path: Path):
        assert self._order_of(cfg_path)[-1] == "session"

    def test_the_session_modules_load_under_the_loading_screen_not_ahead_of_it(self):
        loaded = run_python_the_way_a_launcher_does(
            "import sys, fun_time.orchestrator; "
            "print('fun_time.windows_bridge_orchestrator' in sys.modules)")

        assert loaded.stdout.strip() == "False"

    def test_the_loading_screen_says_the_engine_is_being_checked_while_it_is(self, cfg_path: Path):
        said: list[str] = []
        loading_screen = _a_loading_screen()
        loading_screen.progress.announce.side_effect = said.append

        with _a_launch_with_the_machine_held_off(
            open_the_loading_screen=MagicMock(return_value=loading_screen),
            engine_missing_abort=MagicMock(
                side_effect=lambda *_a, **_k: said.append("probed") or False),
        ):
            main(["--config", str(cfg_path)])

        assert said == ["engine", "probed"]

    def test_a_failed_launch_takes_the_loading_screen_off_the_launchers_failure_dialog(self, cfg_path: Path):
        loading_screen = _a_loading_screen()

        with _a_launch_with_the_machine_held_off(
            open_the_loading_screen=MagicMock(return_value=loading_screen),
            engine_missing_abort=MagicMock(side_effect=OSError("the engine check failed")),
        ), pytest.raises(OSError, match="the engine check failed"):
            main(["--config", str(cfg_path)])

        loading_screen.take_it_down.assert_called_once()

    def test_a_missing_engine_is_handed_the_way_to_uncover_its_alert(self, cfg_path: Path):
        loading_screen = _a_loading_screen()
        abort = MagicMock(return_value=True)

        with _a_launch_with_the_machine_held_off(open_the_loading_screen=MagicMock(return_value=loading_screen),
                       engine_missing_abort=abort):
            result = main(["--config", str(cfg_path)])

        assert result == 1
        assert abort.call_args.kwargs["uncover"] == loading_screen.take_it_down


class TestStartupMarker:
    def test_marker_lives_in_the_state_dir(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert startup_marker_path(cfg) == cfg.paths.state_dir / "launcher.ready"

    def test_signal_writes_the_marker(self, cfg_path: Path):
        cfg = load_config(cfg_path)

        tell_the_hidden_launcher_startup_resolved(cfg)

        assert startup_marker_path(cfg).is_file()

    def test_a_second_launch_can_leave_the_marker_before_any_state_folder_exists(
            self, cfg_factory, tmp_path: Path):
        cfg = load_config(cfg_factory({"paths": {"state_dir": str(tmp_path / "not_yet")}}))

        tell_the_hidden_launcher_startup_resolved(cfg)

        assert startup_marker_path(cfg).is_file()

    def test_a_marker_that_cannot_be_written_never_stops_the_launch(self, cfg_factory, tmp_path: Path):
        a_real_file_where_the_state_dir_belongs = tmp_path / "blocked_state"
        a_real_file_where_the_state_dir_belongs.write_text("", encoding="utf-8")
        cfg = load_config(cfg_factory(
            {"paths": {"state_dir": str(a_real_file_where_the_state_dir_belongs)}}))

        tell_the_hidden_launcher_startup_resolved(cfg)

        assert not startup_marker_path(cfg).exists()

    def test_successful_launch_leaves_the_marker(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off():
            result = main(["--config", str(cfg_path)])

        assert result == 0
        assert startup_marker_path(load_config(cfg_path)).is_file()

    def test_the_launcher_adds_no_failure_dialog_over_the_already_running_message(
            self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off(claim_the_session=MagicMock(return_value=None),
                       show_already_running_message=MagicMock()):
            result = main(["--config", str(cfg_path)])

        assert result == 1
        assert startup_marker_path(load_config(cfg_path)).is_file()

    def test_validation_failure_leaves_no_marker(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off(
            validate_config=MagicMock(side_effect=FileNotFoundError("missing dir")),
        ), pytest.raises(FileNotFoundError):
            main(["--config", str(cfg_path)])

        assert not startup_marker_path(load_config(cfg_path)).exists()

    def test_check_only_run_leaves_no_marker(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off():
            result = main(["--config", str(cfg_path), "--check"])

        assert result == 0
        assert not startup_marker_path(load_config(cfg_path)).exists()

    def test_a_crash_past_the_marker_is_fun_times_to_report_not_the_launchers(self, cfg_path: Path):
        with _a_launch_with_the_machine_held_off(run_windows_bridge=MagicMock(side_effect=RuntimeError("a crash of its own"))), \
             pytest.raises(RuntimeError, match="a crash of its own"):
            main(["--config", str(cfg_path)])

        assert startup_marker_path(load_config(cfg_path)).is_file()


_SESSION_LAYERS = ("orchestrator", "windows_bridge_orchestrator",
                   "windows_bridge_sequencer", "windows_bridge_startup")


def _modules_imported_by(layer: str) -> set[str]:
    tree = ast.parse((REPO_DIR / "fun_time" / f"{layer}.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return imported


class TestTheSessionIsFourLayers:
    def test_no_session_layer_imports_one_above_it(self):
        for depth, layer in enumerate(_SESSION_LAYERS):
            assert not _modules_imported_by(layer) & set(_SESSION_LAYERS[:depth]), layer


class TestALaunchThatNamesNoOrigenerator:
    def test_stops_before_anything_starts_and_says_which_setting_is_missing(self, cfg_factory):
        config_path = cfg_factory({"paths": {"origenerator_dir": ""}})

        with _a_launch_with_the_machine_held_off(
                no_origenerator_set_up=no_origenerator_set_up) as launch:
            result = main(["--config", str(config_path)])

        assert result == 1
        launch.open_the_loading_screen.assert_not_called()
        launch.run_windows_bridge.assert_not_called()
        [said] = launch.show_funestra_died_alert.call_args.args
        assert said == NO_ORIGENERATOR_SET_UP

    def test_a_launch_that_names_one_goes_on(self, cfg_factory, tmp_path):
        config_path = cfg_factory({"paths": {"origenerator_dir": str(tmp_path / "origenerator")}})

        with _a_launch_with_the_machine_held_off(
                no_origenerator_set_up=no_origenerator_set_up) as launch:
            main(["--config", str(config_path)])

        launch.run_windows_bridge.assert_called_once()
        launch.show_funestra_died_alert.assert_not_called()
