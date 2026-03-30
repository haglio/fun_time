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

    # Sources are written to .m3u playlist files to stay under Windows' command-line
    # length limit.  Check the playlist file rather than the command for source paths.
    primary_playlist = tmp_path / "state" / "vlc_primary_playlist.m3u"
    portrait_playlist = tmp_path / "state" / "vlc_portrait_playlist.m3u"
    landscape_playlist = tmp_path / "state" / "vlc_landscape_playlist.m3u"
    assert str(primary_playlist) in first_command
    assert primary_playlist.exists()
    primary_content = primary_playlist.read_text(encoding="utf-8")
    assert "primary_a" in primary_content
    assert "primary_b" in primary_content
    assert portrait_playlist.exists()
    assert landscape_playlist.exists()
    landscape_content = landscape_playlist.read_text(encoding="utf-8")
    assert "landscape_a" in landscape_content
    assert "landscape_b" in landscape_content

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


def test_launch_core_apps_mutes_vlc_when_hide_windows_true(tmp_path: Path):
    """When hide_windows=True, VLC instances are muted via HTTP after pl_next."""
    result_file = tmp_path / "core_apps.ini"

    class FakeProc:
        _counter = 0
        def __init__(self, *_args, **_kwargs):
            FakeProc._counter += 1
            self.pid = FakeProc._counter * 100

    FakeProc._counter = 0
    http_commands: list[tuple[int, str]] = []

    def tracking_vlc_http_cmd(port, cmd, pw):
        http_commands.append((port, cmd))
        return True

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=lambda *a, **kw: FakeProc()), \
         patch("fun_time.windows_bridge_startup.wait_for_http", return_value=True), \
         patch("fun_time.windows_bridge_startup.set_repeat_mode", return_value=True), \
         patch("fun_time.windows_bridge_startup.vlc_http_cmd", side_effect=tracking_vlc_http_cmd), \
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

    # Each VLC should get pl_next followed by volume mute
    mute_cmds = [(port, cmd) for port, cmd in http_commands if cmd == "volume&val=0"]
    assert len(mute_cmds) == 3, f"Expected 3 mute commands, got {mute_cmds}"
    muted_ports = {port for port, _ in mute_cmds}
    assert muted_ports == {8090, 8091, 8092}


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


def test_build_vlc_launch_command_includes_no_video_during_integration(monkeypatch):
    monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", "a.mp4", 8090, "pw", repeat_mode="repeat")
    assert "--no-video" in cmd


def test_build_vlc_launch_command_omits_no_video_outside_integration(monkeypatch):
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", "a.mp4", 8090, "pw", repeat_mode="repeat")
    assert "--no-video" not in cmd


def test_build_vlc_launch_command_includes_volume_zero_when_mute_for_loading(monkeypatch):
    """VLC must start pre-muted during loading (hide_windows=True) so no
    audio blips before the HTTP mute command arrives."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", "a.mp4", 8090, "pw", repeat_mode="repeat", mute=True)
    idx = cmd.index("--volume")
    assert cmd[idx + 1] == "0"


def test_build_vlc_launch_command_never_includes_start_paused(monkeypatch):
    """--start-paused must NEVER be used: VLC re-applies it on every item
    transition, not just startup.  This causes a black screen every time
    the user navigates.  Volume muting alone is sufficient."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    for repeat_mode in ("repeat", "loop"):
        for mute in (True, False):
            cmd = _build_vlc_launch_command("vlc.exe", "a.mp4", 8090, "pw", repeat_mode=repeat_mode, mute=mute)
            assert "--start-paused" not in cmd, \
                f"--start-paused must never appear (repeat_mode={repeat_mode}, mute={mute})"


def test_build_vlc_launch_command_never_includes_random(monkeypatch):
    """--random must never appear: it causes VLC to re-pick a random item on every
    navigation, making pl_play&id=N index arithmetic wrong. Python shuffles the
    sources list at launch instead."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", "a.mp4|b.mp4", 8090, "pw", repeat_mode=repeat_mode)
        assert "--random" not in cmd, f"--random must not appear (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_includes_no_random(monkeypatch):
    """--no-random must always appear to override VLC's saved config (vlcrc).
    Without it, if the user ever manually enabled shuffle in VLC, the setting
    persists and VLC advances randomly instead of sequentially, breaking
    prev/next navigation which relies on sequential playlist order."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", "a.mp4|b.mp4", 8090, "pw", repeat_mode=repeat_mode)
        assert "--no-random" in cmd, f"--no-random must appear to override saved vlcrc (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_shuffles_sources_in_python(monkeypatch):
    """Sources must be shuffled by Python before being passed to VLC (not by VLC's
    --random flag), so the playlist insertion order is the playback order and
    vlc_nav_step's index arithmetic is correct."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    sources = "a.mp4|b.mp4|c.mp4"
    shuffle_called = []

    def fake_shuffle(lst):
        shuffle_called.append(True)
        lst.reverse()  # deterministic stand-in for testing

    with patch("fun_time.windows_bridge_startup.random.shuffle", side_effect=fake_shuffle):
        cmd = _build_vlc_launch_command("vlc.exe", sources, 8090, "pw", repeat_mode="repeat")

    assert shuffle_called, "random.shuffle must be called on sources"
    a_idx, b_idx, c_idx = cmd.index("a.mp4"), cmd.index("b.mp4"), cmd.index("c.mp4")
    assert c_idx < b_idx < a_idx, "Sources should appear in the shuffled (reversed) order"


def test_build_vlc_launch_command_omits_start_paused_for_satellite_vlc_when_muted(monkeypatch):
    """Satellite VLCs (loop mode) must not get --start-paused even when muted.
    --volume 0 already prevents audio blips; --start-paused would cause every
    subsequent playlist item to start paused (VLC applies the flag to every
    item transition), producing a black screen after the first video ends."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", "a.mp4", 8090, "pw", repeat_mode="loop", mute=True)
    assert "--start-paused" not in cmd


def test_build_vlc_launch_command_expands_directory_to_individual_files(tmp_path, monkeypatch):
    """Directory sources must be recursively expanded into individual .mp4 file
    paths. This ensures every video is a leaf item in VLC's playlist so that
    vlc_nav_step can resolve adjacent items by ID.  Without expansion VLC presents
    the directory as a single folder node and vlc_nav_step finds no leaves."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.mp4").touch()
    (tmp_path / "b.mp4").touch()
    (sub / "c.mp4").touch()
    (tmp_path / "ignore.txt").touch()  # non-mp4 must be ignored

    with patch("fun_time.windows_bridge_startup.random.shuffle", side_effect=lambda x: None):
        cmd = _build_vlc_launch_command("vlc.exe", str(tmp_path), 8090, "pw", repeat_mode="loop")

    assert str(tmp_path / "a.mp4") in cmd
    assert str(tmp_path / "b.mp4") in cmd
    assert str(sub / "c.mp4") in cmd
    assert str(tmp_path) not in cmd  # directory itself must not appear
    assert not any("ignore.txt" in arg for arg in cmd)


def test_build_vlc_launch_command_writes_playlist_file_when_playlist_path_given(tmp_path, monkeypatch):
    """When playlist_path is provided, expanded sources must be written to that
    file and only the playlist path added to the command (not individual file paths).
    This is required to stay under Windows' 32 767-character command-line limit
    when there are hundreds of video files."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    playlist_path = tmp_path / "out" / "test.m3u"

    with patch("fun_time.windows_bridge_startup.random.shuffle", side_effect=lambda x: None):
        cmd = _build_vlc_launch_command(
            "vlc.exe", "a.mp4|b.mp4|c.mp4", 8090, "pw", repeat_mode="loop",
            playlist_path=playlist_path,
        )

    # Playlist file path must appear in the command
    assert str(playlist_path) in cmd
    # Individual file paths must NOT appear in the command
    assert "a.mp4" not in cmd
    assert "b.mp4" not in cmd
    assert "c.mp4" not in cmd
    # Playlist file must exist and contain all sources
    assert playlist_path.exists()
    content = playlist_path.read_text(encoding="utf-8")
    assert "a.mp4" in content
    assert "b.mp4" in content
    assert "c.mp4" in content

