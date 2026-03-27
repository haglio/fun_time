from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

from fun_time.windows_bridge_startup import (
    _build_vlc_launch_command,
    launch_core_apps,
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    restart_broker,
    seed_robot_hand_state,
    start_core_session,
)


def test_restart_broker_stops_existing_processes_and_launches_tray(tmp_path: Path):
    project_dir = tmp_path
    launch_path = project_dir / "launch_broker_tray.vbs"
    launch_path.write_text("", encoding="utf-8")

    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess.Popen"
    ) as popen, patch("fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}):
        restart_broker(project_dir)

    run.assert_called_once()
    run_command = run.call_args.args[0]
    assert run_command[:4] == ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden"]
    assert "fun_time\\.broker_app" in run_command[-1]
    assert "launch_broker_tray\\.vbs" in run_command[-1]
    popen.assert_called_once_with(["wscript.exe", str(launch_path)], cwd=project_dir, creationflags=1)


def test_restart_broker_starts_broker_directly_during_integration(tmp_path: Path, monkeypatch):
    project_dir = tmp_path
    config_path = project_dir / "fun_time_integration_config.json"
    config_path.write_text(
        '{"paths": {"python_exe": "pythonw.exe"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(123)
    ) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ), patch("fun_time.windows_bridge_startup.Path.exists", return_value=True):
        restart_broker(project_dir, config_path)

    run.assert_called_once()
    command = popen.call_args.args[0]
    assert command[0].endswith("python.exe")
    assert command[1:3] == ["-m", "fun_time.broker_app"]
    assert command[-2:] == ["--config", str(config_path.resolve())]


def test_prepare_random_favs_browser_manifest_delegates_to_random_browser_builder(tmp_path: Path):
    output_path = tmp_path / "browser_manifest.txt"

    with patch("fun_time.windows_bridge_startup.build_manifest", return_value=("Profile 2", ["https://example.com"])) as build, patch(
        "fun_time.windows_bridge_startup.write_manifest"
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


def test_start_core_session_runs_broker_seed_manifest_and_core_launch(tmp_path: Path):
    result_file = tmp_path / "core_session.ini"

    with patch("fun_time.windows_bridge_startup.restart_broker") as restart, patch(
        "fun_time.windows_bridge_startup.seed_robot_hand_state"
    ) as seed, patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ) as prepare, patch("fun_time.windows_bridge_startup.launch_core_apps") as launch:
        start_core_session(
            project_dir=tmp_path,
            config_path="fun_time_config.json",
            random_favs_browser_manifest_file=tmp_path / "browser_manifest.txt",
            enabled_file=tmp_path / "robot_hand_enabled.txt",
            paused_file=tmp_path / "robot_hand_paused.txt",
            audio_paused_file=tmp_path / "audio_paused.txt",
            vlc_exe="vlc.exe",
            mfp_exe="mfp.exe",
            primary_sources="primary_a|primary_b",
            portrait_sources="portrait_a",
            landscape_sources="landscape_a",
            primary_port=8090,
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
        )

    restart.assert_called_once_with(tmp_path, "fun_time_config.json")
    seed.assert_called_once()
    prepare.assert_called_once_with("fun_time_config.json", tmp_path / "browser_manifest.txt")
    launch.assert_called_once_with(
        project_dir=tmp_path,
        vlc_exe="vlc.exe",
        mfp_exe="mfp.exe",
        primary_sources="primary_a|primary_b",
        portrait_sources="portrait_a",
        landscape_sources="landscape_a",
        primary_port=8090,
        portrait_port=8091,
        landscape_port=8092,
        password="pw",
        result_file=result_file,
        hide_windows=False,
    )


def test_launch_ui_companions_starts_dashboard_robot_and_audio_and_writes_result(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=[FakeProc(11), FakeProc(22), FakeProc(33)]) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        launch_ui_companions(
            python_exe="python.exe",
            dashboard_module="fun_time.dashboard_app",
            dashboard_enabled=True,
            windows_bridge_manifest_path="windows_bridge_launch.ini",
            dashboard_x=10,
            dashboard_y=20,
            dashboard_width=30,
            dashboard_height=40,
            mfp_pid=55,
            robot_hand_module="fun_time.robot_hand.app",
            audio_module="fun_time.audio_companion_app",
            config_path="cfg.json",
            clips_folder="clips",
            audio_folder="audio",
            robot_x=100,
            robot_y=200,
            robot_width=300,
            robot_height=400,
            result_file=result_file,
        )

    assert popen.call_count == 3
    dashboard_command = popen.call_args_list[0].args[0]
    assert dashboard_command[:3] == ["python.exe", "-m", "fun_time.dashboard_app"]
    assert "--mfp-pid" in dashboard_command
    robot_command = popen.call_args_list[1].args[0]
    assert robot_command[:3] == ["python.exe", "-m", "fun_time.robot_hand.app"]
    audio_command = popen.call_args_list[2].args[0]
    assert audio_command[:3] == ["python.exe", "-m", "fun_time.audio_companion_app"]

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "dashboard_pid") == "11"
    assert parser.get("result", "robot_hand_pid") == "22"
    assert parser.get("result", "audio_pid") == "33"


def test_launch_ui_companions_skips_dashboard_when_disabled(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=[FakeProc(22), FakeProc(33)]) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        launch_ui_companions(
            python_exe="python.exe",
            dashboard_module="fun_time.dashboard_app",
            dashboard_enabled=False,
            windows_bridge_manifest_path="windows_bridge_launch.ini",
            dashboard_x=10,
            dashboard_y=20,
            dashboard_width=30,
            dashboard_height=40,
            mfp_pid=55,
            robot_hand_module="fun_time.robot_hand.app",
            audio_module="fun_time.audio_companion_app",
            config_path="cfg.json",
            clips_folder="clips",
            audio_folder="audio",
            robot_x=100,
            robot_y=200,
            robot_width=300,
            robot_height=400,
            result_file=result_file,
        )

    assert popen.call_count == 2
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "dashboard_pid") == "0"
    assert parser.get("result", "robot_hand_pid") == "22"
    assert parser.get("result", "audio_pid") == "33"


def test_launch_core_apps_starts_media_stack_waits_and_writes_result(tmp_path: Path):
    result_file = tmp_path / "core_apps.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=[FakeProc(101), FakeProc(202), FakeProc(303), FakeProc(404)]) as popen, patch(
        "fun_time.windows_bridge_startup.wait_for_http", return_value=True
    ) as wait_http, patch(
        "fun_time.windows_bridge_startup.set_repeat_mode", return_value=True
    ) as set_repeat, patch("fun_time.windows_bridge_startup.vlc_http_cmd", return_value=True) as vlc_cmd, patch(
        "fun_time.windows_bridge_startup.time.sleep"
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


def test_launch_core_apps_launches_minimized_when_hide_windows_true(tmp_path: Path):
    """When hide_windows=True, VLC and MFP launch with SW_SHOWMINNOACTIVE to prevent flash."""
    result_file = tmp_path / "core_apps.ini"

    class FakeProc:
        _counter = 0
        def __init__(self, *_args, **_kwargs):
            FakeProc._counter += 1
            self.pid = FakeProc._counter * 100

    FakeProc._counter = 0
    popen_kwargs_list: list[dict] = []

    def capturing_popen(*args, **kwargs):
        popen_kwargs_list.append(kwargs)
        return FakeProc()

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=capturing_popen), \
         patch("fun_time.windows_bridge_startup.wait_for_http", return_value=True), \
         patch("fun_time.windows_bridge_startup.set_repeat_mode", return_value=True), \
         patch("fun_time.windows_bridge_startup.vlc_http_cmd", return_value=True), \
         patch("fun_time.windows_bridge_startup.time.sleep"):
        launch_core_apps(
            project_dir=tmp_path,
            vlc_exe="vlc.exe",
            mfp_exe="mfp.exe",
            primary_sources="a",
            portrait_sources="b",
            landscape_sources="c",
            primary_port=8090,
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
            hide_windows=True,
        )

    # All 4 launches should use SW_SHOWMINNOACTIVE (7) to prevent flash
    import subprocess as sp
    for i, kw in enumerate(popen_kwargs_list):
        si = kw.get("startupinfo")
        assert si is not None, f"Launch {i} missing startupinfo"
        assert si.wShowWindow == 7, f"Launch {i} wShowWindow={si.wShowWindow}, expected 7 (SW_SHOWMINNOACTIVE)"


def test_start_core_session_passes_hide_windows_through(tmp_path: Path):
    """start_core_session forwards hide_windows to launch_core_apps."""
    result_file = tmp_path / "core_session.ini"

    with patch("fun_time.windows_bridge_startup.restart_broker"), patch(
        "fun_time.windows_bridge_startup.seed_robot_hand_state"
    ), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch("fun_time.windows_bridge_startup.launch_core_apps") as launch:
        start_core_session(
            project_dir=tmp_path,
            config_path="cfg.json",
            random_favs_browser_manifest_file=tmp_path / "m.txt",
            enabled_file=tmp_path / "e.txt",
            paused_file=tmp_path / "p.txt",
            audio_paused_file=tmp_path / "a.txt",
            vlc_exe="vlc.exe",
            mfp_exe="mfp.exe",
            primary_sources="a",
            portrait_sources="b",
            landscape_sources="c",
            primary_port=8090,
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
            hide_windows=True,
        )

    assert launch.call_args.kwargs["hide_windows"] is True


def test_build_vlc_launch_command_includes_volume_zero_when_mute_env_set(monkeypatch):
    monkeypatch.setenv("FUN_TIME_MUTE_AUDIO", "1")
    cmd = _build_vlc_launch_command("vlc.exe", "a.mp4|b.mp4", 8090, "pw", repeat_mode="repeat")
    idx = cmd.index("--volume")
    assert cmd[idx + 1] == "0"
    assert "--repeat" in cmd


def test_build_vlc_launch_command_omits_volume_when_mute_env_unset(monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", "a.mp4", 8090, "pw", repeat_mode="loop")
    assert "--volume" not in cmd

