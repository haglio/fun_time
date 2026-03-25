from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

from fun_time.controller_startup import (
    launch_core_apps,
    launch_runtime_companions,
    prepare_random_favs_browser_manifest,
    restart_broker,
    seed_robot_hand_state,
)


def test_restart_broker_stops_existing_processes_and_launches_tray(tmp_path: Path):
    project_dir = tmp_path
    launch_path = project_dir / "launch_broker_tray.vbs"
    launch_path.write_text("", encoding="utf-8")

    with patch("fun_time.controller_startup.subprocess.run") as run, patch(
        "fun_time.controller_startup.subprocess.Popen"
    ) as popen, patch("fun_time.controller_startup.subprocess_window_kwargs", return_value={"creationflags": 1}):
        restart_broker(project_dir)

    run.assert_called_once()
    run_command = run.call_args.args[0]
    assert run_command[:4] == ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden"]
    assert "fun_time\\.broker_app" in run_command[-1]
    assert "launch_broker_tray\\.vbs" in run_command[-1]
    popen.assert_called_once_with(["wscript.exe", str(launch_path)], cwd=project_dir, creationflags=1)


def test_prepare_random_favs_browser_manifest_delegates_to_random_browser_builder(tmp_path: Path):
    output_path = tmp_path / "browser_manifest.txt"

    with patch("fun_time.controller_startup.build_manifest", return_value=("Profile 2", ["https://example.com"])) as build, patch(
        "fun_time.controller_startup.write_manifest"
    ) as write:
        prepare_random_favs_browser_manifest("config.json", output_path)

    build.assert_called_once_with("config.json")
    write.assert_called_once_with(output_path, "Profile 2", ["https://example.com"])


def test_seed_robot_hand_state_writes_enabled_and_paused_files(tmp_path: Path):
    enabled_file = tmp_path / "robot_hand_enabled.txt"
    paused_file = tmp_path / "robot_hand_paused.txt"
    audio_file = tmp_path / "audio_paused.txt"

    seed_robot_hand_state(enabled_file, paused_file, audio_file)

    assert enabled_file.read_text(encoding="utf-8") == "1"
    assert paused_file.read_text(encoding="utf-8") == "1"
    assert audio_file.read_text(encoding="utf-8") == "1"


def test_launch_runtime_companions_starts_robot_and_audio_and_writes_result(tmp_path: Path):
    result_file = tmp_path / "runtime_companions.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.controller_startup.subprocess.Popen", side_effect=[FakeProc(111), FakeProc(222)]) as popen, patch(
        "fun_time.controller_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        launch_runtime_companions(
            python_exe="python.exe",
            robot_hand_module="fun_time.robot_hand.app",
            audio_module="fun_time.audio_companion_app",
            config_path="cfg.json",
            clips_folder="clips",
            audio_folder="audio",
            x=10,
            y=20,
            width=30,
            height=40,
            result_file=result_file,
        )

    assert popen.call_count == 2
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "robot_hand_pid") == "111"
    assert parser.get("result", "audio_pid") == "222"


def test_launch_core_apps_starts_media_stack_waits_and_writes_result(tmp_path: Path):
    result_file = tmp_path / "core_apps.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.controller_startup.subprocess.Popen", side_effect=[FakeProc(101), FakeProc(202), FakeProc(303), FakeProc(404)]) as popen, patch(
        "fun_time.controller_startup.wait_for_http", return_value=True
    ) as wait_http, patch(
        "fun_time.controller_startup.set_repeat_mode", return_value=True
    ) as set_repeat, patch("fun_time.controller_startup.vlc_http_cmd", return_value=True) as vlc_cmd, patch(
        "fun_time.controller_startup.time.sleep"
    ):
        launch_core_apps(
            project_dir=tmp_path,
            vlc_exe="vlc.exe",
            mfp_exe="mfp.exe",
            primary_sources="primary_a|primary_b",
            portrait_sources="portrait_a",
            landscape_sources="landscape_a|landscape_b",
            primary_port=8090,
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
        )

    assert popen.call_count == 4
    first_command = popen.call_args_list[0].args[0]
    assert first_command[:2] == ["vlc.exe", "--no-one-instance"]
    assert "primary_a" in first_command
    assert "primary_b" in first_command
    wait_http.assert_any_call(8090, "pw", 7000)
    wait_http.assert_any_call(8091, "pw", 7000)
    wait_http.assert_any_call(8092, "pw", 7000)
    set_repeat.assert_any_call(8091, "pw", "all")
    set_repeat.assert_any_call(8092, "pw", "all")
    vlc_cmd.assert_any_call(8090, "pl_next", "pw")
    vlc_cmd.assert_any_call(8091, "pl_next", "pw")
    vlc_cmd.assert_any_call(8092, "pl_next", "pw")

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "primary_pid") == "101"
    assert parser.get("result", "mfp_pid") == "202"
    assert parser.get("result", "portrait_pid") == "303"
    assert parser.get("result", "landscape_pid") == "404"
