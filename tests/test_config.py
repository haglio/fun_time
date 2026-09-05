"""Tests for fun_time.config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time import config
from fun_time.config import ProjectConfig, load_config
from fun_time.loopback_server import LOOPBACK_PORT

# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_loads_valid_config(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert isinstance(cfg, ProjectConfig)

    def test_raises_on_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_missing_default_regenerates_from_example_then_raises(self, tmp_path: Path, monkeypatch):
        # Root cause #1: the git-ignored fun_time_config.json got swept away and
        # startup died on an opaque FileNotFoundError.  A missing *default*
        # config now writes a starter copy from the committed example (so there
        # is a file to fill in) and still stops — the example's paths are
        # placeholders that must not be taken for a real library — with a clear
        # message naming the file.  Expectation derived from the committed
        # example so this holds on a public checkout.
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
        example = tmp_path / "fun_time_config.example.json"  # deliberately absent
        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", target)
        monkeypatch.setattr(config, "EXAMPLE_CONFIG_PATH", example)

        with pytest.raises(FileNotFoundError, match="fun_time_config.example.json"):
            load_config()
        assert not target.exists()

    def test_missing_explicit_path_raises_without_regenerating(self, tmp_path: Path, monkeypatch):
        # A caller naming a specific file that isn't there gets a clear error,
        # and nothing is written in its place — regeneration is only for the
        # default config.
        default = tmp_path / "fun_time_config.json"
        example = tmp_path / "fun_time_config.example.json"
        example.write_text(config.EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", default)
        monkeypatch.setattr(config, "EXAMPLE_CONFIG_PATH", example)

        missing = tmp_path / "somewhere-else.json"
        with pytest.raises(FileNotFoundError, match="somewhere-else.json"):
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

    def test_raises_when_nau_library_dirs_empty(self, cfg_factory):
        path = cfg_factory({"paths": {"nau_library_dirs": []}})
        with pytest.raises(ValueError, match="nau_library_dirs"):
            load_config(path)

    def test_nau_library_dirs_not_a_list(self, cfg_factory):
        path = cfg_factory({"paths": {"nau_library_dirs": "not-a-list"}})
        with pytest.raises(TypeError, match="nau_library_dirs"):
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

    def test_the_broker_shares_our_state_dir_unless_it_is_named(self, cfg_path: Path):
        """Which every session but a branch one does — one directory, one broker."""
        cfg = load_config(cfg_path)
        assert cfg.paths.broker_state_dir == cfg.paths.state_dir

    def test_the_brokers_files_follow_the_broker_when_it_is_named(self, cfg_factory, tmp_path: Path):
        """Named, it takes the whole channel with it, not the heartbeat alone.

        Each of these is a file ``../broker`` opens from its own config, so all
        of them have to leave together — a session reading four out of five from
        the broker and one from itself is the bug in miniature.
        """
        broker_state = tmp_path / "primary" / "state"
        path = cfg_factory({"paths": {"broker_state_dir": str(broker_state)}})
        cfg = load_config(path)

        assert cfg.paths.broker_state_dir == broker_state
        assert cfg.paths.state_dir != broker_state
        assert [
            cfg.broker_heartbeat_file,
            cfg.osr2_serial_rx_file,
            cfg.broker_cmd_file,
            cfg.genau_mode_file,
            cfg.genau_enabled_file,
        ] == [
            broker_state / "broker_heartbeat.txt",
            broker_state / "osr2_serial_rx.txt",
            broker_state / "broker_cmd.txt",
            broker_state / "genau_mode.txt",
            broker_state / "genau_enabled.txt",
        ]

    def test_missing_random_favs_browser_section_defaults_disabled(self, cfg_factory):
        path = cfg_factory()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("random_favs_browser", None)
        raw.pop("chrome_overlay", None)
        path.write_text(json.dumps(raw), encoding="utf-8")

        cfg = load_config(path)
        assert cfg.random_favs_browser.enabled is False


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
        """The defaults used to be one machine's own shortcut and profile, so a
        section saying only ``enabled`` launched Chrome into somebody's profile
        by guess.  Nobody's to guess: off until the section says whose, and the
        shortcut placeholder is a file the launch validation refuses by name."""
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

    def test_legacy_chrome_overlay_section_still_loads_random_favs_browser_settings(self, cfg_factory):
        path = cfg_factory(
            {
                "chrome_overlay": {
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

    def test_a_singular_dir_key_is_read_as_a_one_folder_list(self, cfg_path: Path, tmp_path: Path):
        """README offers `portrait_dir` beside `portrait_dirs` for the one-folder
        case, and the test config is written the singular way."""
        cfg = load_config(cfg_path)
        assert cfg.paths.portrait_dirs == ((tmp_path / "videos" / "videos" / "portrait").resolve(),)
        assert cfg.paths.landscape_dirs == ((tmp_path / "videos" / "videos" / "landscape").resolve(),)

    def test_multiple_nau_library_dirs(self, tmp_path: Path, cfg_factory):
        extra = tmp_path / "extra"
        extra.mkdir()
        path = cfg_factory({"paths": {"nau_library_dirs": [
            str(tmp_path / "nau_library"),
            str(extra),
        ]}})
        cfg = load_config(path)
        assert len(cfg.paths.nau_library_dirs) == 2

    def test_multiple_portrait_and_landscape_dirs(self, tmp_path: Path, cfg_factory):
        portrait_extra = tmp_path / "portrait_extra"
        landscape_extra = tmp_path / "landscape_extra"
        portrait_extra.mkdir()
        landscape_extra.mkdir()
        path = cfg_factory({"paths": {
            "portrait_dirs": [str(tmp_path / "portrait"), str(portrait_extra)],
            "landscape_dirs": [str(tmp_path / "landscape"), str(landscape_extra)],
        }})
        cfg = load_config(path)
        assert len(cfg.paths.portrait_dirs) == 2
        assert len(cfg.paths.landscape_dirs) == 2
        assert cfg.paths.portrait_dirs[0] == (tmp_path / "portrait").resolve()
        assert cfg.paths.landscape_dirs[0] == (tmp_path / "landscape").resolve()


# ---------------------------------------------------------------------------
# ProjectConfig derived properties
# ---------------------------------------------------------------------------

class TestProjectConfigProperties:
    def test_log_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.log_file("broker") == (tmp_path / "state" / "broker.log").resolve()

    def test_genau_mode_file(self, cfg_path: Path, tmp_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.genau_mode_file == (tmp_path / "state" / "genau_mode.txt").resolve()

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


# ---------------------------------------------------------------------------
# VoiceControlConfig
# ---------------------------------------------------------------------------

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

    def test_raises_on_wrong_type(self, cfg_factory):
        path = cfg_factory({"voice_control": "not-a-dict"})
        with pytest.raises(TypeError):
            load_config(path)


# ---------------------------------------------------------------------------
# loopback_port
# ---------------------------------------------------------------------------

class TestLoopbackPort:
    """The loopback server's port is the last fixed port a session claims.

    It is machine-wide, so a second session — an integration run, above all —
    finds it busy and comes up without a loopback server at all: Tampermonkey
    stops auto-updating and the RFB tab pages never hear about OmniPause, with
    only a log line to say so.  Naming it in config is what lets a run take one
    of its own; the default stays the port the userscript's @updateURL is pinned
    to, so nothing about a real session changes.
    """

    def test_defaults_to_the_port_the_userscript_is_pinned_to(self, cfg_path: Path):
        cfg = load_config(cfg_path)
        assert cfg.loopback_port == LOOPBACK_PORT

    def test_a_session_can_be_given_a_port_of_its_own(self, cfg_factory):
        path = cfg_factory({"loopback_port": 54321})
        cfg = load_config(path)
        assert cfg.loopback_port == 54321


# ---------------------------------------------------------------------------
# instance_id
# ---------------------------------------------------------------------------

class TestInstanceId:
    """Which running session a config *is*, for the single-instance mutex.

    The mutex used to be taken on the config path directly, which made "one
    session per config file" the only arrangement expressible.  A
    branch-verification session needs the opposite: its own config, but the live
    session's identity, so the two can never both hold the desktop's AHK shell,
    monitors and fixed ports.  An integration run needs the old behavior and
    keeps it — its config lives at a unique temp path, so it is its own instance
    without saying anything.
    """

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


class TestTheProjectsOwnPaths:
    """Where this checkout is, computed once instead of at six call sites.

    Each copy silently encoded that its module sits exactly one directory below
    the root, so a module that moved a level took its icon path with it and
    failed at run time rather than at import.
    """

    def test_the_icon_sits_at_the_root_of_this_checkout(self):
        from fun_time.project_paths import PROJECT_DIR, PROJECT_ICON

        assert PROJECT_ICON == PROJECT_DIR / "icon.ico"
        assert PROJECT_ICON.is_file()

    def test_the_root_is_the_one_the_config_resolves_against(self):
        """One root, so a relative path in the config and the icon on the
        window's title bar cannot come from two different checkouts."""
        from fun_time.config import PROJECT_DIR as CONFIG_ROOT
        from fun_time.project_paths import PROJECT_DIR

        assert CONFIG_ROOT is PROJECT_DIR

    def test_every_module_that_wants_the_icon_asks_for_that_one(self):
        """Five of the six copies; `satellite.app` keeps its own, and its
        comment says why.  Read from the source, because three of the five are
        uses rather than bindings and an alias would not show them."""
        import ast

        from fun_time.project_paths import PROJECT_DIR

        # FunTimeVR's window wears the V, so it asks for the other constant --
        # still project_paths', still not a path it spells itself.
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

    def test_and_the_one_that_keeps_its_own_really_imports_nothing_from_fun_time(self):
        """`satellite/` imports nothing from `fun_time`, and one constant is
        not worth inverting that."""
        import ast

        from fun_time.project_paths import PROJECT_DIR
        from satellite.app import ICON_PATH

        assert ICON_PATH == PROJECT_DIR / "icon.ico"
        tree = ast.parse((PROJECT_DIR / "satellite" / "app.py").read_text(encoding="utf-8"))
        imported = [
            name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in ([node.module] if isinstance(node, ast.ImportFrom)
                         else [alias.name for alias in node.names])
        ]
        assert not [name for name in imported if name and name.split(".")[0] == "fun_time"]
