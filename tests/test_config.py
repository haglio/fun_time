from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from app_support import haglio_settings, ports
from app_support.flick_folder import flat_flicks_in

from fun_time import config
from fun_time.config import PROJECT_DIR as CONFIG_ROOT
from fun_time.config import ProjectConfig, load_config
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.loopback_server import LOOPBACK_PORT
from fun_time.project_paths import PROJECT_DIR, PROJECT_ICON
from tests.made_up_genau import point_the_shared_settings_at


class TestLoadConfig:
    def test_loads_valid_config(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert isinstance(cfg, ProjectConfig)

    def test_raises_on_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_missing_default_regenerates_from_example_then_raises(self, tmp_path: Path, monkeypatch):
        example_text = config.EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
        example = tmp_path / "fun_time_config.example.json"
        example.write_text(example_text, encoding="utf-8")
        target = tmp_path / "fun_time_config.json"
        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", target)
        monkeypatch.setattr(config, "EXAMPLE_CONFIG_PATH", example)

        assert not target.exists()
        with pytest.raises(FileNotFoundError, match="fun_time_config"):
            load_config()
        assert target.exists()
        assert json.loads(target.read_text(encoding="utf-8")) == json.loads(example_text)

    def test_missing_default_without_example_raises_naming_both(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "fun_time_config.json"
        example = tmp_path / "fun_time_config.example.json"
        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", target)
        monkeypatch.setattr(config, "EXAMPLE_CONFIG_PATH", example)

        with pytest.raises(FileNotFoundError, match=r"fun_time_config\.example\.json"):
            load_config()
        assert not target.exists()

    def test_missing_explicit_path_raises_without_regenerating(self, tmp_path: Path, monkeypatch):
        default = tmp_path / "fun_time_config.json"
        example = tmp_path / "fun_time_config.example.json"
        example.write_text(config.EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", default)
        monkeypatch.setattr(config, "EXAMPLE_CONFIG_PATH", example)

        missing = tmp_path / "somewhere-else.json"
        with pytest.raises(FileNotFoundError, match=r"somewhere-else\.json"):
            load_config(missing)
        assert not missing.exists()
        assert not default.exists()

    def test_raises_on_missing_section(self, tmp_path: Path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text(json.dumps({"paths": {}}), encoding="utf-8")
        with pytest.raises((ValueError, KeyError, TypeError)):
            load_config(cfg_file)

    def test_raises_when_paths_is_not_dict(self, tmp_path: Path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text(json.dumps({"paths": "nope"}), encoding="utf-8")
        with pytest.raises(TypeError):
            load_config(cfg_file)

    def test_raises_when_main_funestra_library_dirs_empty(self, cfg_factory):
        path = cfg_factory({"paths": {"main_player_library_dirs": []}})
        with pytest.raises(ValueError, match="main_player_library_dirs"):
            load_config(path)

    def test_main_funestra_library_dirs_not_a_list(self, cfg_factory):
        path = cfg_factory({"paths": {"main_player_library_dirs": "not-a-list"}})
        with pytest.raises(TypeError, match="main_player_library_dirs"):
            load_config(path)

    def test_loads_paths_correctly(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.ahk_exe == tmp_path / "ahk.exe"
        assert cfg.paths.state_dir == (tmp_path / "state").resolve()

    def test_loads_layout(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.layout.primary_monitor == 1
        assert cfg.layout.secondary_monitor == 2

    def test_loads_audio_companion(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_companion.host == "127.0.0.1"
        assert cfg.audio_companion.port == 50556

    def test_broker_tray_launcher_defaults_to_none(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.broker_tray_launcher is None

    def test_broker_tray_launcher_resolves_when_set(self, cfg_factory, tmp_path: Path):
        launcher = tmp_path / "launch_broker_tray.vbs"
        path = cfg_factory({"paths": {"broker_tray_launcher": str(launcher)}})
        cfg = load_config(path)
        assert cfg.paths.broker_tray_launcher == launcher

    def test_the_sibling_checkouts_are_the_ones_the_config_names(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"paths": {"sibling_checkouts": [str(tmp_path / "funestra")]}})
        assert load_config(path).paths.sibling_checkouts == (tmp_path / "funestra",)

    def test_the_sibling_checkouts_make_one_path_value_in_the_order_named(self, cfg_factory, tmp_path: Path):
        first, second = tmp_path / "funestra", tmp_path / "shared_ui"
        path = cfg_factory({"paths": {"sibling_checkouts": [str(first), str(second)]}})
        assert load_config(path).paths.sibling_checkouts_path == f"{first}{os.pathsep}{second}"

    def test_a_config_from_before_the_rename_still_names_them(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"paths": {"genau_project_dirs": [str(tmp_path / "funestra")]}})
        assert load_config(path).paths.sibling_checkouts == (tmp_path / "funestra",)

    def test_the_new_name_wins_where_a_config_says_both(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"paths": {"sibling_checkouts": [str(tmp_path / "new")],
                                      "genau_project_dirs": [str(tmp_path / "old")]}})
        assert load_config(path).paths.sibling_checkouts == (tmp_path / "new",)

    def test_the_broker_shares_our_state_dir_unless_it_is_named(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.broker_state_dir == cfg.paths.state_dir

    def test_the_brokers_files_follow_the_broker_when_it_is_named(self, cfg_factory, tmp_path: Path):
        broker_state = tmp_path / "primary" / "state"
        path = cfg_factory({"paths": {"broker_state_dir": str(broker_state)}})
        cfg = load_config(path)

        assert cfg.paths.broker_state_dir == broker_state
        assert cfg.paths.state_dir != broker_state
        assert [
            cfg.broker_cmd_file,
            cfg.broker_mode_file,
        ] == [
            broker_state / "broker_cmd.txt",
            broker_state / "broker_mode.txt",
        ]

    def test_missing_random_favs_browser_section_defaults_disabled(self, cfg_factory):
        path = cfg_factory()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("random_favs_browser", None)
        path.write_text(json.dumps(raw), encoding="utf-8")

        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is False


class TestTheOsr2Slot:
    def test_is_the_brokers_own_where_the_config_names_none(self, cfg_path: Path):
        assert load_config(cfg_path).osr2_slot_port == ports.OSR2_SLOT

    def test_is_the_one_the_config_names(self, cfg_factory):
        assert load_config(cfg_factory({"osr2_slot_port": 50600})).osr2_slot_port == 50600


class TestRegenConfig:
    def test_defaults_when_section_absent(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.regen.generate_video_url == "https://example.com/video"
        assert cfg.regen.generate_image_url == "https://example.com/create"
        assert cfg.regen.media_root is None
        assert cfg.regen.metadata_root is None

    def test_reads_values_when_present(self, cfg_factory, tmp_path: Path):
        path = cfg_factory({"regen": {
            "media_root": str(tmp_path / "media"),
            "metadata_root": str(tmp_path / "meta"),
        }})
        cfg = load_config(path)
        assert cfg.regen.media_root == tmp_path / "media"
        assert cfg.regen.metadata_root == tmp_path / "meta"
        assert cfg.random_favs_browser.profile_name == ""
        assert cfg.random_favs_browser.enabled is False

    def test_a_browser_section_that_names_no_profile_stays_off(self, cfg_factory):
        path = cfg_factory({"random_favs_browser": {"enabled": True, "open_count": 3}})
        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is False
        assert cfg.random_favs_browser.profile_name == ""
        assert cfg.random_favs_browser.shortcut_path.name == "random_favs_browser.lnk"
        assert cfg.random_favs_browser.open_count == 3

    def test_loads_random_favs_browser_settings(self, cfg_factory):
        path = cfg_factory(
            {
                "random_favs_browser": {
                    "enabled": True,
                    "shortcut_path": "chrome.exe",
                    "user_data_dir": "chrome_data",
                    "profile_name": "Jane Doe",

                    "open_count": 7,
                }
            }
        )
        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is True
        assert cfg.random_favs_browser.shortcut_path.name == "chrome.exe"
        assert cfg.random_favs_browser.user_data_dir.name == "chrome_data"
        assert cfg.random_favs_browser.open_count == 7
        assert cfg.random_favs_browser.lazy_load is False
        assert not hasattr(cfg.random_favs_browser, "bookmarks_folder_name")

    def test_loads_random_favs_browser_lazy_load(self, cfg_factory):
        path = cfg_factory(
            {
                "random_favs_browser": {
                    "enabled": True,
                    "shortcut_path": "chrome.exe",
                    "user_data_dir": "chrome_data",
                    "profile_name": "Jane Doe",

                    "open_count": 7,
                    "lazy_load": True,
                }
            }
        )
        cfg = load_config(path)
        assert cfg.random_favs_browser.lazy_load is True

    def test_multiple_main_funestra_library_dirs(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"main_player_library_dirs": [
            str(tmp_path / "main_player_library"),
            str(extra),
        ]}})
        cfg = load_config(path)
        assert len(cfg.paths.main_funestra_library_dirs) == 2


class TestKinosFolder:
    def test_the_satellites_play_and_trash_into_the_kino_folder_all_of_haglio_shares(
            self, cfg_path: Path, tmp_path: Path, monkeypatch):
        kino = tmp_path / "kino"
        settings = tmp_path / "kino_settings.json"
        settings.write_text(json.dumps({**haglio_settings.load(), "kino_dir": str(kino)}), encoding="utf-8")
        monkeypatch.setenv(haglio_settings.SETTINGS_ENV, str(settings))

        cfg = load_config(cfg_path)

        assert (cfg.paths.portrait_dirs, cfg.paths.landscape_dirs, cfg.paths.trash_dir) == (
            (kino / "2D" / "AI" / "portrait",), (kino / "2D" / "AI" / "landscape",), kino / "trash")

    def test_folders_left_in_the_config_file_are_not_where_kino_is(self, cfg_factory, tmp_path: Path):
        cfg = load_config(cfg_factory({"paths": {
            "portrait_dirs": [str(tmp_path / "old portrait")], "trash_dir": str(tmp_path / "old trash")}}))

        assert tmp_path / "old portrait" not in cfg.paths.portrait_dirs
        assert cfg.paths.trash_dir != tmp_path / "old trash"


class TestGenausFolders:
    def test_the_settings_all_of_haglio_shares_say_where_genaus_flicks_and_audio_are(
            self, cfg_path: Path, tmp_path: Path, monkeypatch):
        genau = tmp_path / "another_genau"
        flat_flicks_in(genau / "made_up_name").mkdir(parents=True)
        audio = tmp_path / "somewhere_else" / "sounds"
        point_the_shared_settings_at(monkeypatch, genau, audio)

        cfg = load_config(cfg_path)

        assert (cfg.paths.genau_dir, cfg.paths.flicks_dir, cfg.paths.genau_audio_dir) == (
            genau, genau / "made_up_name", audio)

    def test_a_genau_dir_left_in_the_config_file_is_not_where_genau_is(
            self, cfg_factory, genaus_folder: Path, tmp_path: Path):
        old = tmp_path / "old_genau"
        flat_flicks_in(old / "old_name").mkdir(parents=True)

        cfg = load_config(cfg_factory({"paths": {"genau_dir": str(old)}}))

        assert cfg.paths.genau_dir == genaus_folder


class TestProjectConfigProperties:
    def test_log_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.log_file("broker") == (tmp_path / "state" / "broker.log").resolve()

    def test_broker_mode_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.broker_mode_file == (tmp_path / "state" / "broker_mode.txt").resolve()

    def test_genau_cmd_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.genau_cmd_file == (tmp_path / "state" / "genau_cmd.txt").resolve()

    def test_genau_paused_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.genau_paused_file == (tmp_path / "state" / "genau_paused.txt").resolve()

    def test_audio_paused_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_paused_file == (tmp_path / "state" / "audio_paused.txt").resolve()

    def test_audio_volume_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.audio_volume_file == (tmp_path / "state" / "audio_volume.txt").resolve()

    def test_logs_dir(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.logs_dir == (tmp_path / "state").resolve()

    def test_random_favs_browser_manifest_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.random_favs_browser_manifest_file == (tmp_path / "state" / "random_favs_browser_urls.txt").resolve()


class TestVoiceControlConfig:
    def test_missing_section_defaults_disabled(self, cfg_factory):
        path = cfg_factory()
        cfg = load_config(path)
        assert cfg.voice_control.enabled is False

    def test_loads_when_present(self, cfg_factory):
        path = cfg_factory({
            "voice_control": {
                "enabled": True,
                "model_path": "vosk-model-small-en-us-0.15",
                "sample_rate": 8000,
                "confidence_threshold": 0.6,
                "device_name": "Brio",
            },
        })
        cfg = load_config(path)
        assert cfg.voice_control.enabled is True
        assert cfg.voice_control.model_path == "vosk-model-small-en-us-0.15"
        assert cfg.voice_control.sample_rate == 8000
        assert cfg.voice_control.confidence_threshold == 0.6
        assert cfg.voice_control.device_name == "Brio"

    def test_a_config_that_names_no_model_listens_with_the_mid_size_one(self, cfg_factory):
        cfg = load_config(cfg_factory({"voice_control": {"enabled": True}}))

        assert cfg.voice_control.model_path == "vosk-model-en-us-0.22-lgraph"

    def test_raises_on_wrong_type(self, cfg_factory):
        path = cfg_factory({"voice_control": "not-a-dict"})
        with pytest.raises(TypeError):
            load_config(path)

    def test_commands_wait_for_the_verifying_listener_unless_the_config_says_otherwise(
            self, cfg_factory):
        assert load_config(cfg_factory({"voice_control": {"enabled": True}})
                           ).voice_control.confirm_commands is True
        assert load_config(cfg_factory({"voice_control": {"confirm_commands": False}})
                           ).voice_control.confirm_commands is False


class TestLoopbackPort:
    def test_defaults_to_the_port_the_userscript_is_pinned_to(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.loopback_port == LOOPBACK_PORT

    def test_a_session_can_be_given_a_port_of_its_own(self, cfg_factory):
        path = cfg_factory({"loopback_port": 54321})
        cfg = load_config(path)
        assert cfg.loopback_port == 54321


class TestInstanceId:
    def test_defaults_to_the_config_file(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.instance_id == str(cfg_path)

    def test_a_config_can_name_another_sessions_identity(self, cfg_factory):
        path = cfg_factory({"instance_id": "C:/checkouts/fun_time/fun_time_config.json"})
        cfg = load_config(path)
        assert cfg.instance_id == "C:/checkouts/fun_time/fun_time_config.json"


class TestOrigeneratorPaths:
    def test_absent_keys_mean_no_origenerator(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.paths.origenerator_dir is None
        assert cfg.paths.origenerator_python_exe is None

    def test_origenerator_keys_resolve_as_paths(self, cfg_factory, tmp_path: Path):
        cfg = load_config(cfg_factory({"paths": {
            "origenerator_dir": str(tmp_path / "origenerator"),
            "origenerator_python_exe": str(tmp_path / "py" / "python.exe"),
        }}))
        assert cfg.paths.origenerator_dir == (tmp_path / "origenerator").resolve()
        assert cfg.paths.origenerator_python_exe == (tmp_path / "py" / "python.exe").resolve()

    def test_origenerator_channel_files_live_in_the_state_dir(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        state = (tmp_path / "state").resolve()
        assert cfg.origenerator_cmd_file == state / "origenerator_cmd.txt"
        assert cfg.origenerator_paused_file == state / "origenerator_paused.txt"
        assert cfg.origenerator_status_file == state / "origenerator_status.txt"

    def test_the_hosted_apps_window_is_handed_over_through_the_state_dir(self, cfg_path):
        cfg = load_config(cfg_path)

        assert cfg.origenerator_frames_file == (
            cfg.paths.state_dir / "origenerator_frame.bin")
        assert cfg.origenerator_input_file == (
            cfg.paths.state_dir / "origenerator_input.txt")

    def test_each_satellites_hosted_hud_lives_in_the_state_dir(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        state = (tmp_path / "state").resolve()
        assert cfg.satellite(FunTimeFunestra.PORTRAIT).origenerator_hud_file == (
            state / "origenerator_portrait_hud.json")
        assert cfg.satellite(FunTimeFunestra.LANDSCAPE).origenerator_hud_file == (
            state / "origenerator_landscape_hud.json")

    def test_each_satellite_publishes_its_messages_in_the_state_dir(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        state = (tmp_path / "state").resolve()
        assert cfg.satellite(FunTimeFunestra.PORTRAIT).notice_file == state / "portrait_notice.txt"
        assert cfg.satellite(FunTimeFunestra.LANDSCAPE).notice_file == state / "landscape_notice.txt"


class TestTheProjectsOwnPaths:
    def test_the_icon_sits_at_the_root_of_this_checkout(self):
        assert PROJECT_ICON == PROJECT_DIR / "icon.ico"
        assert PROJECT_ICON.is_file()

    def test_the_root_is_the_one_the_config_resolves_against(self):
        assert CONFIG_ROOT is PROJECT_DIR

    def test_every_module_that_wants_the_icon_asks_for_that_one(self):
        wants = {
            "fun_time/process_identity.py": "PROJECT_ICON",
            "fun_time_vr/vr_session.py": "PROJECT_VR_ICON",
            "fun_time/overlay_window.py": "PROJECT_ICON",
            "fun_time/dashboard_app.py": "PROJECT_ICON",
        }
        for name, constant in sorted(wants.items()):
            tree = ast.parse((PROJECT_DIR / name).read_text(encoding="utf-8"))
            named = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} \
                | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            assert constant in named, name
            recomputed = [
                n for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and n.value in ("icon.ico", "vr_icon.ico")]
            assert recomputed == [], f"{name} still spells the path itself"

    def test_a_satellite_spells_no_icon_path_and_imports_nothing_from_fun_time(self):
        source = (PROJECT_DIR / "satellite" / "app.py").read_text(encoding="utf-8")
        assert "icon.ico" not in source
        tree = ast.parse((PROJECT_DIR / "satellite" / "app.py").read_text(encoding="utf-8"))
        imported = [
            name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in ([node.module] if isinstance(node, ast.ImportFrom)
                         else [alias.name for alias in node.names])
        ]
        assert not [name for name in imported if name and name.split(".")[0] == "fun_time"]
