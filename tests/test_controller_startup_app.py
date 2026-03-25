from __future__ import annotations

from fun_time.controller_startup_app import build_parser, main


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


def test_build_parser_accepts_launch_runtime_companions_command():
    args = build_parser().parse_args(
        [
            "launch-runtime-companions",
            "--python-exe",
            "python.exe",
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
            "--x",
            "10",
            "--y",
            "20",
            "--width",
            "30",
            "--height",
            "40",
            "--result-file",
            "state\\runtime_companions.ini",
        ]
    )

    assert args.command == "launch-runtime-companions"
    assert args.width == 30


def test_main_dispatches_restart_broker(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_restart(project_dir: str) -> None:
        recorded["project_dir"] = project_dir

    monkeypatch.setattr("fun_time.controller_startup_app.restart_broker", fake_restart)

    code = main(["restart-broker", "--project-dir", "C:\\FunTime"])

    assert code == 0
    assert recorded["project_dir"] == "C:\\FunTime"


def test_main_dispatches_manifest_prep(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_prepare(config_path: str, output_path: str) -> None:
        recorded["config"] = config_path
        recorded["output"] = output_path

    monkeypatch.setattr("fun_time.controller_startup_app.prepare_random_favs_browser_manifest", fake_prepare)

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

    monkeypatch.setattr("fun_time.controller_startup_app.seed_robot_hand_state", fake_seed)

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


def test_main_dispatches_launch_runtime_companions(monkeypatch):
    recorded: dict[str, object] = {}

    def fake_launch(**kwargs) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("fun_time.controller_startup_app.launch_runtime_companions", fake_launch)

    code = main(
        [
            "launch-runtime-companions",
            "--python-exe",
            "python.exe",
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
            "--x",
            "10",
            "--y",
            "20",
            "--width",
            "30",
            "--height",
            "40",
            "--result-file",
            "state\\runtime_companions.ini",
        ]
    )

    assert code == 0
    assert recorded["python_exe"] == "python.exe"
    assert recorded["width"] == 30
    assert recorded["result_file"] == "state\\runtime_companions.ini"
