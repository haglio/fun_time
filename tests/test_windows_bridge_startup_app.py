from __future__ import annotations

from fun_time.windows_bridge_startup_app import build_parser, main


def test_build_parser_accepts_restart_broker_command():
    args = build_parser().parse_args(["restart-broker", "--project-dir", "C:\\FunTime"])

    assert args.command == "restart-broker"
    assert args.project_dir == "C:\\FunTime"


def test_build_parser_accepts_manifest_command():
    args = build_parser().parse_args(
        [
            "prepare-random-favs-browser-manifest",
            "--config",
            "fun_time_config.json",
            "--output",
            "state\\random_favs_browser_urls.txt",
        ]
    )

    assert args.command == "prepare-random-favs-browser-manifest"
    assert args.config == "fun_time_config.json"


def test_build_parser_accepts_seed_robot_hand_state_command():
    args = build_parser().parse_args(
        [
            "seed-robot-hand-state",
            "--enabled-file",
            "state\\robot_hand_enabled.txt",
            "--paused-file",
            "state\\robot_hand_paused.txt",
            "--audio-paused-file",
            "state\\audio_paused.txt",
        ]
    )

    assert args.command == "seed-robot-hand-state"
    assert args.enabled_file.endswith("robot_hand_enabled.txt")


def test_build_parser_accepts_start_core_session_command():
    args = build_parser().parse_args(
        [
            "start-core-session",
            "--project-dir",
            "C:\\FunTime",
            "--config",
            "fun_time_config.json",
            "--random-favs-browser-manifest-file",
            "state\\random_favs_browser_urls.txt",
            "--enabled-file",
            "state\\robot_hand_enabled.txt",
            "--paused-file",
            "state\\robot_hand_paused.txt",
            "--audio-paused-file",
            "state\\audio_paused.txt",
            "--vlc-exe",
            "vlc.exe",
            "--mfp-exe",
            "mfp.exe",
            "--primary-sources",
            "primary_a|primary_b",
            "--portrait-sources",
            "portrait_a",
            "--landscape-sources",
            "landscape_a",
            "--primary-port",
            "8090",
            "--portrait-port",
            "8091",
            "--landscape-port",
            "8092",
            "--password",
            "pw",
            "--result-file",
            "state\\core_session.ini",
        ]
    )

    assert args.command == "start-core-session"
    assert args.primary_port == 8090
    assert args.random_favs_browser_manifest_file.endswith("random_favs_browser_urls.txt")


def test_build_parser_accepts_launch_ui_companions_command():
    args = build_parser().parse_args(
        [
            "launch-ui-companions",
            "--python-exe",
            "python.exe",
            "--dashboard-module",
            "fun_time.dashboard_app",
            "--dashboard-enabled",
            "1",
            "--windows-bridge-manifest-path",
            "windows_bridge_launch.ini",
            "--dashboard-x",
            "10",
            "--dashboard-y",
            "20",
            "--dashboard-width",
            "30",
            "--dashboard-height",
            "40",
            "--mfp-pid",
            "55",
            "--robot-hand-module",
            "fun_time.robot_hand.app",
            "--audio-module",
            "fun_time.audio_companion_app",
            "--config",
            "fun_time_config.json",
            "--clips-folder",
            "clips",
            "--audio-folder",
            "audio",
            "--robot-x",
            "100",
            "--robot-y",
            "200",
            "--robot-width",
            "300",
            "--robot-height",
            "400",
            "--result-file",
            "state\\ui_companions.ini",
        ]
    )

    assert args.command == "launch-ui-companions"
    assert args.dashboard_width == 30
    assert args.robot_height == 400


def test_build_parser_accepts_launch_core_apps_command():
    args = build_parser().parse_args(
        [
            "launch-core-apps",
            "--project-dir",
            "C:\\FunTime",
            "--vlc-exe",
            "vlc.exe",
            "--mfp-exe",
            "mfp.exe",
            "--primary-sources",
            "primary_a|primary_b",
            "--portrait-sources",
            "portrait_a",
            "--landscape-sources",
            "landscape_a",
            "--primary-port",
            "8090",
            "--portrait-port",
            "8091",
            "--landscape-port",
            "8092",
            "--password",
            "pw",
            "--result-file",
            "state\\core_apps.ini",
        ]
    )

    assert args.command == "launch-core-apps"
    assert args.primary_port == 8090


def test_main_dispatches_restart_broker(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_restart(project_dir: str) -> None:
        recorded["project_dir"] = project_dir

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.restart_broker", fake_restart)

    code = main(["restart-broker", "--project-dir", "C:\\FunTime"])

    assert code == 0
    assert recorded["project_dir"] == "C:\\FunTime"


def test_main_dispatches_manifest_prep(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_prepare(config_path: str, output_path: str) -> None:
        recorded["config"] = config_path
        recorded["output"] = output_path

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.prepare_random_favs_browser_manifest", fake_prepare)

    code = main(
        [
            "prepare-random-favs-browser-manifest",
            "--config",
            "fun_time_config.json",
            "--output",
            "state\\random_favs_browser_urls.txt",
        ]
    )

    assert code == 0
    assert recorded["config"] == "fun_time_config.json"
    assert recorded["output"] == "state\\random_favs_browser_urls.txt"


def test_main_dispatches_seed_robot_hand_state(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_seed(enabled_file: str, paused_file: str, audio_paused_file: str) -> None:
        recorded["enabled"] = enabled_file
        recorded["paused"] = paused_file
        recorded["audio"] = audio_paused_file

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.seed_robot_hand_state", fake_seed)

    code = main(
        [
            "seed-robot-hand-state",
            "--enabled-file",
            "state\\robot_hand_enabled.txt",
            "--paused-file",
            "state\\robot_hand_paused.txt",
            "--audio-paused-file",
            "state\\audio_paused.txt",
        ]
    )

    assert code == 0
    assert recorded["enabled"] == "state\\robot_hand_enabled.txt"
    assert recorded["paused"] == "state\\robot_hand_paused.txt"
    assert recorded["audio"] == "state\\audio_paused.txt"


def test_start_core_session_reads_from_manifest(monkeypatch, cfg_factory, tmp_path):
    from fun_time.windows_bridge_manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
    from fun_time.config import load_config

    recorded: dict[str, object] = {}

    def fake_start(**kwargs) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.start_core_session", fake_start)

    cfg = load_config(cfg_factory())
    manifest_path = write_windows_bridge_manifest(
        cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    result_file = tmp_path / "result.ini"

    code = main([
        "start-core-session",
        "--manifest", str(manifest_path),
        "--result-file", str(result_file),
    ])

    assert code == 0
    assert recorded["result_file"] == str(result_file)
    assert recorded["password"] == "testpw"
    assert recorded["primary_port"] == cfg.controller.primary_vlc_http_port
    assert recorded["vlc_exe"] == str(cfg.paths.vlc_exe)


def test_launch_ui_companions_reads_from_manifest(monkeypatch, cfg_factory, tmp_path):
    from fun_time.windows_bridge_manifest import write_windows_bridge_manifest, WINDOWS_BRIDGE_MANIFEST_FILENAME
    from fun_time.config import load_config

    recorded: dict[str, object] = {}

    def fake_launch(**kwargs) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.launch_ui_companions", fake_launch)

    cfg = load_config(cfg_factory())
    manifest_path = write_windows_bridge_manifest(
        cfg, "testpw", tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
    )
    result_file = tmp_path / "result.ini"

    code = main([
        "launch-ui-companions",
        "--manifest", str(manifest_path),
        "--result-file", str(result_file),
        "--mfp-pid", "1234",
        "--dashboard-x", "10", "--dashboard-y", "20",
        "--dashboard-width", "100", "--dashboard-height", "50",
        "--robot-x", "30", "--robot-y", "40",
        "--robot-width", "200", "--robot-height", "300",
    ])

    assert code == 0
    assert recorded["mfp_pid"] == 1234
    assert recorded["dashboard_x"] == 10
    assert recorded["robot_width"] == 200
    assert recorded["result_file"] == str(result_file)
    assert recorded["python_exe"] == str(cfg.paths.python_exe)


def test_main_dispatches_start_core_session(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_start(**kwargs) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.start_core_session", fake_start)

    code = main(
        [
            "start-core-session",
            "--project-dir",
            "C:\\FunTime",
            "--config",
            "fun_time_config.json",
            "--random-favs-browser-manifest-file",
            "state\\random_favs_browser_urls.txt",
            "--enabled-file",
            "state\\robot_hand_enabled.txt",
            "--paused-file",
            "state\\robot_hand_paused.txt",
            "--audio-paused-file",
            "state\\audio_paused.txt",
            "--vlc-exe",
            "vlc.exe",
            "--mfp-exe",
            "mfp.exe",
            "--primary-sources",
            "primary_a|primary_b",
            "--portrait-sources",
            "portrait_a",
            "--landscape-sources",
            "landscape_a",
            "--primary-port",
            "8090",
            "--portrait-port",
            "8091",
            "--landscape-port",
            "8092",
            "--password",
            "pw",
            "--result-file",
            "state\\core_session.ini",
        ]
    )

    assert code == 0
    assert recorded["project_dir"] == "C:\\FunTime"
    assert recorded["config_path"] == "fun_time_config.json"
    assert recorded["random_favs_browser_manifest_file"] == "state\\random_favs_browser_urls.txt"
    assert recorded["result_file"] == "state\\core_session.ini"


def test_main_dispatches_launch_ui_companions(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_launch(**kwargs) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.launch_ui_companions", fake_launch)

    code = main(
        [
            "launch-ui-companions",
            "--python-exe",
            "python.exe",
            "--dashboard-module",
            "fun_time.dashboard_app",
            "--dashboard-enabled",
            "1",
            "--windows-bridge-manifest-path",
            "windows_bridge_launch.ini",
            "--dashboard-x",
            "10",
            "--dashboard-y",
            "20",
            "--dashboard-width",
            "30",
            "--dashboard-height",
            "40",
            "--mfp-pid",
            "55",
            "--robot-hand-module",
            "fun_time.robot_hand.app",
            "--audio-module",
            "fun_time.audio_companion_app",
            "--config",
            "fun_time_config.json",
            "--clips-folder",
            "clips",
            "--audio-folder",
            "audio",
            "--robot-x",
            "100",
            "--robot-y",
            "200",
            "--robot-width",
            "300",
            "--robot-height",
            "400",
            "--result-file",
            "state\\ui_companions.ini",
        ]
    )

    assert code == 0
    assert recorded["python_exe"] == "python.exe"
    assert recorded["dashboard_module"] == "fun_time.dashboard_app"
    assert recorded["dashboard_enabled"] is True
    assert recorded["windows_bridge_manifest_path"] == "windows_bridge_launch.ini"
    assert recorded["mfp_pid"] == 55
    assert recorded["robot_height"] == 400


def test_main_dispatches_launch_core_apps(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_launch(**kwargs) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("fun_time.windows_bridge_startup_app.launch_core_apps", fake_launch)

    code = main(
        [
            "launch-core-apps",
            "--project-dir",
            "C:\\FunTime",
            "--vlc-exe",
            "vlc.exe",
            "--mfp-exe",
            "mfp.exe",
            "--primary-sources",
            "primary_a|primary_b",
            "--portrait-sources",
            "portrait_a",
            "--landscape-sources",
            "landscape_a",
            "--primary-port",
            "8090",
            "--portrait-port",
            "8091",
            "--landscape-port",
            "8092",
            "--password",
            "pw",
            "--result-file",
            "state\\core_apps.ini",
        ]
    )

    assert code == 0
    assert recorded["project_dir"] == "C:\\FunTime"
    assert recorded["vlc_exe"] == "vlc.exe"
    assert recorded["mfp_exe"] == "mfp.exe"
    assert recorded["portrait_port"] == 8091


