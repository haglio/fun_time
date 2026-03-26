from __future__ import annotations

from fun_time.windows_bridge_startup_app import build_parser, main


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


def test_build_parser_accepts_start_core_session_with_manifest():
    args = build_parser().parse_args(
        [
            "start-core-session",
            "--manifest",
            "windows_bridge_launch.ini",
            "--result-file",
            "state\\core_session.ini",
        ]
    )

    assert args.command == "start-core-session"
    assert args.manifest == "windows_bridge_launch.ini"
    assert args.result_file == "state\\core_session.ini"


def test_build_parser_accepts_launch_ui_companions_with_manifest():
    args = build_parser().parse_args(
        [
            "launch-ui-companions",
            "--manifest",
            "windows_bridge_launch.ini",
            "--dashboard-x", "10",
            "--dashboard-y", "20",
            "--dashboard-width", "30",
            "--dashboard-height", "40",
            "--mfp-pid", "55",
            "--robot-x", "100",
            "--robot-y", "200",
            "--robot-width", "300",
            "--robot-height", "400",
            "--result-file",
            "state\\ui_companions.ini",
        ]
    )

    assert args.command == "launch-ui-companions"
    assert args.manifest == "windows_bridge_launch.ini"
    assert args.dashboard_width == 30
    assert args.robot_height == 400


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
