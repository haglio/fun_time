from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

from fun_time.modes import FModePlaylistPlan
from fun_time.modes import SatelliteLibraryContext
from fun_time.windows_bridge_startup import (
    _VLC_HTTP_BIND_TIMEOUT_MS,
    _await_vlc_http,
    _build_vlc_launch_command,
    launch_core_apps,
    launch_genau,
    launch_nau,
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    restart_broker,
    seed_paused_states,
    start_core_session,
)


def test_restart_broker_stops_and_launches_tray(tmp_path: Path):
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    launcher.parent.mkdir()
    launcher.touch()

    with patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop, \
         patch("fun_time.windows_bridge_startup.time.sleep") as sleep, \
         patch("fun_time.windows_bridge_startup.subprocess.Popen") as popen, \
         patch("fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}):
        restart_broker(tmp_path, launcher)

    stop.assert_called_once_with(tmp_path)
    sleep.assert_called_once_with(0.4)
    popen.assert_called_once_with(
        ["wscript.exe", str(launcher)], cwd=launcher.parent, creationflags=1,
    )


def test_restart_broker_skips_launch_when_no_launcher(tmp_path: Path):
    with patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop, \
         patch("fun_time.windows_bridge_startup.time.sleep"), \
         patch("fun_time.windows_bridge_startup.subprocess.Popen") as popen:
        restart_broker(tmp_path)

    stop.assert_called_once_with(tmp_path)
    popen.assert_not_called()


def test_prepare_random_favs_browser_manifest_delegates_to_random_browser_builder(tmp_path: Path):
    output_path = tmp_path / "browser_manifest.txt"

    with patch("fun_time.windows_bridge_startup.build_manifest", return_value=("Profile 2", ["https://example.com"])) as build, patch(
        "fun_time.windows_bridge_startup.write_manifest"
    ) as write:
        prepare_random_favs_browser_manifest("config.json", output_path)

    build.assert_called_once_with("config.json")
    write.assert_called_once_with(output_path, "Profile 2", ["https://example.com"])


def test_seed_paused_states_writes_all_three_flags(tmp_path: Path):
    genau_file = tmp_path / "genau_paused.txt"
    audio_file = tmp_path / "audio_paused.txt"
    nau_file = tmp_path / "nau_paused.txt"

    seed_paused_states(genau_file, audio_file, nau_file)

    # Genau parked, audio parked, Nau paused until the sequencer's reveal.
    assert genau_file.read_text(encoding="utf-8") == "1"
    assert audio_file.read_text(encoding="utf-8") == "1"
    assert nau_file.read_text(encoding="utf-8") == "1"


def _fake_playlist_plan(state_dir: Path) -> FModePlaylistPlan:
    return FModePlaylistPlan(
        success=True,
        primary_count=2,
        portrait_count=1,
        landscape_count=1,
        portrait_playlist_path=state_dir / "portrait_vlc_playlist.m3u",
        landscape_playlist_path=state_dir / "landscape_vlc_playlist.m3u",
        nau_playlist_path=state_dir / "nau_playlist.tsv",
    )


def test_start_core_session_runs_broker_seed_playlists_and_core_launch(tmp_path: Path):
    result_file = tmp_path / "core_session.ini"
    state_dir = tmp_path / "state"
    plan = _fake_playlist_plan(state_dir)

    with patch("fun_time.windows_bridge_startup.restart_broker") as restart, patch(
        "fun_time.windows_bridge_startup.seed_paused_states"
    ) as seed, patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ) as prepare, patch(
        "fun_time.windows_bridge_startup.build_fmode_playlists", return_value=plan
    ) as build, patch("fun_time.windows_bridge_startup.launch_core_apps") as launch:
        start_core_session(
            project_dir=tmp_path,
            config_path="fun_time_config.json",
            random_favs_browser_manifest_file=tmp_path / "browser_manifest.txt",
            genau_paused_file=tmp_path / "genau_paused.txt",
            audio_paused_file=tmp_path / "audio_paused.txt",
            nau_paused_file=tmp_path / "nau_paused.txt",
            vlc_exe="vlc.exe",
            primary_sources="primary_a|primary_b",
            portrait_sources="portrait_a",
            landscape_sources="landscape_a",
            favs_file=tmp_path / "favs.csv",
            state_dir=state_dir,
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
            provider_media_root=tmp_path / "media",
            provider_metadata_root=tmp_path / "metadata",
        )

    restart.assert_called_once_with(tmp_path, None)
    seed.assert_called_once_with(
        tmp_path / "genau_paused.txt",
        tmp_path / "audio_paused.txt",
        tmp_path / "nau_paused.txt",
    )
    prepare.assert_called_once_with("fun_time_config.json", tmp_path / "browser_manifest.txt")
    # The same playlist builder the F-mode toggle uses, with F-mode off.
    build.assert_called_once_with(
        primary_sources="primary_a|primary_b",
        portrait_sources="portrait_a",
        landscape_sources="landscape_a",
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        enabled=False,
        library=SatelliteLibraryContext(
            media_root=tmp_path / "media",
            metadata_root=tmp_path / "metadata",
            watch_stats_file=state_dir / "watch_stats.json",
        ),
    )
    launch.assert_called_once_with(
        project_dir=tmp_path,
        vlc_exe="vlc.exe",
        portrait_playlist=plan.portrait_playlist_path,
        landscape_playlist=plan.landscape_playlist_path,
        portrait_port=8091,
        landscape_port=8092,
        password="pw",
        result_file=result_file,
        hide_windows=False,
    )


def test_start_core_session_passes_hide_windows_through(tmp_path: Path):
    """start_core_session forwards hide_windows to launch_core_apps."""
    result_file = tmp_path / "core_session.ini"

    with patch("fun_time.windows_bridge_startup.restart_broker"), patch(
        "fun_time.windows_bridge_startup.seed_paused_states"
    ), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_fmode_playlists",
        return_value=_fake_playlist_plan(tmp_path / "state"),
    ), patch("fun_time.windows_bridge_startup.launch_core_apps") as launch:
        start_core_session(
            project_dir=tmp_path,
            config_path="cfg.json",
            random_favs_browser_manifest_file=tmp_path / "m.txt",
            genau_paused_file=tmp_path / "p.txt",
            audio_paused_file=tmp_path / "a.txt",
            nau_paused_file=tmp_path / "n.txt",
            vlc_exe="vlc.exe",
            primary_sources="a",
            portrait_sources="b",
            landscape_sources="c",
            favs_file=tmp_path / "favs.csv",
            state_dir=tmp_path / "state",
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
            hide_windows=True,
        )

    assert launch.call_args.kwargs["hide_windows"] is True


def test_launch_genau_starts_process_and_returns_pid(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(42)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        pid = launch_genau(
            python_exe="python.exe",
            genau_module="fun_time.genau.app",
            config_path="cfg.json",
            clips_folder="clips",
            genau_x=100,
            genau_y=200,
            genau_width=300,
            genau_height=400,
        )

    assert pid == 42
    command = popen.call_args.args[0]
    assert command[:3] == ["python.exe", "-m", "fun_time.genau.app"]
    assert "--config" in command
    assert "--clips-folder" in command


def test_launch_genau_forwards_command_and_paused_files(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(42)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        pid = launch_genau(
            python_exe="python.exe",
            genau_module="fun_time.genau.app",
            config_path="cfg.json",
            clips_folder="clips",
            genau_x=100,
            genau_y=200,
            genau_width=300,
            genau_height=400,
            command_file="state/genau_cmd.txt",
            paused_file="state/genau_paused.txt",
        )

    assert pid == 42
    command = popen.call_args.args[0]
    assert "--command-file" in command
    idx = command.index("--command-file")
    assert command[idx + 1] == "state/genau_cmd.txt"
    assert "--paused-file" in command
    idx = command.index("--paused-file")
    assert command[idx + 1] == "state/genau_paused.txt"


def test_launch_genau_passes_fun_time_flag(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(42)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        launch_genau(
            python_exe="python.exe",
            genau_module="genau.app",
            config_path="cfg.json",
            clips_folder="clips",
            genau_x=0,
            genau_y=0,
            genau_width=800,
            genau_height=600,
        )

    command = popen.call_args.args[0]
    assert "--fun-time" in command


def test_launch_nau_starts_process_and_returns_pid(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(43)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        pid = launch_nau(
            python_exe="python.exe",
            nau_module="nau",
            config_path="cfg.json",
            playlist_file="state/nau_playlist.tsv",
            command_file="state/nau_cmd.txt",
            paused_file="state/nau_paused.txt",
            status_file="state/nau_status.txt",
            nau_x=100,
            nau_y=200,
            nau_width=300,
            nau_height=400,
        )

    assert pid == 43
    assert popen.call_args.kwargs == {"creationflags": 1}
    assert popen.call_args.args[0] == [
        "python.exe",
        "-m",
        "nau",
        "--config",
        "cfg.json",
        "--playlist",
        "state/nau_playlist.tsv",
        "--command-file",
        "state/nau_cmd.txt",
        "--paused-file",
        "state/nau_paused.txt",
        "--status-file",
        "state/nau_status.txt",
        "--x",
        "100",
        "--y",
        "200",
        "--width",
        "300",
        "--height",
        "400",
    ]


def test_launch_ui_companions_launches_dashboard_and_audio(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=[FakeProc(11), FakeProc(33)]) as popen, patch(
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
            rfb_x=5,
            rfb_y=44,
            rfb_width=31,
            rfb_height=96,
            audio_module="fun_time.audio_companion_app",
            config_path="cfg.json",
            audio_folder="audio",
            result_file=result_file,
        )

    assert popen.call_count == 2
    dashboard_command = popen.call_args_list[0].args[0]
    assert dashboard_command == [
        "python.exe",
        "-m",
        "fun_time.dashboard_app",
        "windows_bridge_launch.ini",
        "--x",
        "10",
        "--y",
        "20",
        "--width",
        "30",
        "--height",
        "40",
        "--rfb-x",
        "5",
        "--rfb-y",
        "44",
        "--rfb-width",
        "31",
        "--rfb-height",
        "96",
    ]
    audio_command = popen.call_args_list[1].args[0]
    assert audio_command == [
        "python.exe",
        "-m",
        "fun_time.audio_companion_app",
        "--config",
        "cfg.json",
        "--audio-folder",
        "audio",
    ]

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "dashboard_pid") == "11"
    assert parser.get("result", "audio_pid") == "33"
    assert set(parser["result"].keys()) == {"dashboard_pid", "audio_pid"}


def test_launch_ui_companions_skips_dashboard_when_disabled(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=[FakeProc(33)]) as popen, patch(
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
            rfb_x=5,
            rfb_y=44,
            rfb_width=31,
            rfb_height=96,
            audio_module="fun_time.audio_companion_app",
            config_path="cfg.json",
            audio_folder="audio",
            result_file=result_file,
        )

    assert popen.call_count == 1
    audio_command = popen.call_args_list[0].args[0]
    assert audio_command[:3] == ["python.exe", "-m", "fun_time.audio_companion_app"]

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "dashboard_pid") == "0"
    assert parser.get("result", "audio_pid") == "33"


class _FakeVlcProc:
    """Stand-in for a VLC Popen handle with a controllable liveness signal."""

    def __init__(self, *, alive: bool = True, returncode: int = 0):
        self._alive = alive
        self.returncode = returncode

    def poll(self):
        return None if self._alive else self.returncode


def test_await_vlc_http_raises_with_exit_code_when_process_dies_before_binding():
    """A VLC that exits before its HTTP interface binds must fail immediately
    with its exit code, not wait out the full bind timeout."""
    proc = _FakeVlcProc(alive=False, returncode=3)
    with patch("fun_time.windows_bridge_startup.wait_for_http", return_value=False):
        with pytest.raises(RuntimeError, match="exited.*3"):
            _await_vlc_http(8091, "pw", proc, "Portrait")


def test_await_vlc_http_raises_timeout_when_process_alive_but_unresponsive():
    """A VLC that stays alive but never binds HTTP must raise a timeout error,
    distinct from the exit-code path so the failure is diagnosable."""
    proc = _FakeVlcProc(alive=True)
    with patch("fun_time.windows_bridge_startup.wait_for_http", return_value=False):
        with pytest.raises(RuntimeError, match="did not come up"):
            _await_vlc_http(8091, "pw", proc, "Landscape")


def test_await_vlc_http_returns_when_http_binds():
    """When wait_for_http succeeds, the helper returns without raising and does
    not consult the exit code."""
    proc = _FakeVlcProc(alive=True)
    with patch("fun_time.windows_bridge_startup.wait_for_http", return_value=True) as wait_http:
        _await_vlc_http(8091, "pw", proc, "Portrait")
    wait_http.assert_called_once_with(8091, "pw", _VLC_HTTP_BIND_TIMEOUT_MS, is_alive=ANY)


def test_launch_core_apps_starts_media_stack_waits_and_writes_result(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    result_file = tmp_path / "core_apps.ini"
    portrait_playlist = tmp_path / "state" / "portrait_vlc_playlist.m3u"
    landscape_playlist = tmp_path / "state" / "landscape_vlc_playlist.m3u"

    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=[FakeProc(202), FakeProc(303)]) as popen, patch(
        "fun_time.windows_bridge_startup.wait_for_http", return_value=True
    ) as wait_http, patch(
        "fun_time.windows_bridge_startup.set_repeat_mode", return_value=True
    ) as set_repeat, patch(
        "fun_time.windows_bridge_startup.vlc_http_cmd", return_value=True
    ) as vlc_cmd, patch(
        "fun_time.windows_bridge_startup.replace_playlist_from_file", return_value=True
    ), patch(
        "fun_time.windows_bridge_startup.time.sleep"
    ):
        launch_core_apps(
            project_dir=tmp_path,
            vlc_exe="vlc.exe",
            portrait_playlist=portrait_playlist,
            landscape_playlist=landscape_playlist,
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
        )

    # Exactly two VLC processes now: portrait and landscape (the primary VLC is gone).
    assert popen.call_count == 2
    portrait_command = popen.call_args_list[0].args[0]
    landscape_command = popen.call_args_list[1].args[0]
    assert portrait_command[:2] == ["vlc.exe", "--no-one-instance"]
    # Satellites get their playlist on the command line in the unmuted path.
    assert str(portrait_playlist) in portrait_command
    assert str(landscape_playlist) in landscape_command
    assert "--loop" in portrait_command
    assert "--loop" in landscape_command

    wait_http.assert_any_call(8091, "pw", _VLC_HTTP_BIND_TIMEOUT_MS, is_alive=ANY)
    wait_http.assert_any_call(8092, "pw", _VLC_HTTP_BIND_TIMEOUT_MS, is_alive=ANY)
    set_repeat.assert_any_call(8091, "pw", "all")
    set_repeat.assert_any_call(8092, "pw", "all")
    vlc_cmd.assert_any_call(8091, "pl_next", "pw")
    vlc_cmd.assert_any_call(8092, "pl_next", "pw")

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "portrait_pid") == "202"
    assert parser.get("result", "landscape_pid") == "303"
    assert set(parser["result"].keys()) == {"portrait_pid", "landscape_pid"}


def test_launch_core_apps_mutes_and_defers_playlists_when_hide_windows_true(tmp_path: Path, monkeypatch):
    """When hide_windows=True, VLC instances must:
    1. Launch with no media on the command line so there is nothing to hear
    2. Get muted via HTTP
    3. Have their playlist enqueued (not played) via replace_playlist_from_file
    4. NOT receive pl_next, pl_pause, or pl_play — VLC must be completely idle
    """
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    result_file = tmp_path / "core_apps.ini"
    playlists = {
        8091: tmp_path / "state" / "portrait_vlc_playlist.m3u",
        8092: tmp_path / "state" / "landscape_vlc_playlist.m3u",
    }

    class FakeProc:
        _counter = 0

        def __init__(self, *_args, **_kwargs):
            FakeProc._counter += 1
            self.pid = FakeProc._counter * 100

    FakeProc._counter = 0
    http_commands: list[tuple[int, str]] = []
    replace_calls: list[tuple[int, str, dict]] = []

    def tracking_vlc_http_cmd(port, cmd, pw):
        http_commands.append((port, cmd))
        return True

    def tracking_replace_playlist(port, pw, playlist_path, **kwargs):
        replace_calls.append((port, str(playlist_path), kwargs))
        return True

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=lambda *a, **kw: FakeProc()) as popen, \
         patch("fun_time.windows_bridge_startup.wait_for_http", return_value=True), \
         patch("fun_time.windows_bridge_startup.set_repeat_mode", return_value=True), \
         patch("fun_time.windows_bridge_startup.vlc_http_cmd", side_effect=tracking_vlc_http_cmd), \
         patch("fun_time.windows_bridge_startup.replace_playlist_from_file", side_effect=tracking_replace_playlist), \
         patch("fun_time.windows_bridge_startup.time.sleep"):
        launch_core_apps(
            project_dir=tmp_path,
            vlc_exe="vlc.exe",
            portrait_playlist=playlists[8091],
            landscape_playlist=playlists[8092],
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
            hide_windows=True,
        )

    # VLC commands must NOT contain .m3u playlist paths (deferred)
    for call in popen.call_args_list:
        cmd = call.args[0] if call.args else call.kwargs.get("args", [])
        if isinstance(cmd, list) and cmd and cmd[0] == "vlc.exe":
            assert not any(arg.endswith(".m3u") for arg in cmd), \
                f"Playlist must not be on VLC command line when hide_windows=True: {cmd}"

    # Each VLC should get volume mute
    mute_cmds = [(port, cmd) for port, cmd in http_commands if cmd == "volume&val=0"]
    assert len(mute_cmds) == 2, f"Expected 2 mute commands, got {mute_cmds}"
    muted_ports = {port for port, _ in mute_cmds}
    assert muted_ports == {8091, 8092}

    # Playlists must be enqueued (not played) via replace_playlist_from_file
    assert len(replace_calls) == 2, f"Expected 2 playlist loads, got {replace_calls}"
    for port, path, kwargs in replace_calls:
        assert path == str(playlists[port])
        assert kwargs.get("enqueue_only") is True, \
            f"enqueue_only must be True to prevent playback during loading: {kwargs}"

    # VLC must be completely idle — no pl_next, no pl_pause, no pl_play
    playback_cmds = [(port, cmd) for port, cmd in http_commands
                     if cmd in ("pl_next", "pl_pause", "pl_play")]
    assert playback_cmds == [], \
        f"No playback commands allowed during loading screen: {playback_cmds}"


def test_launch_core_apps_defers_playlists_when_mute_audio_env_set(tmp_path: Path, monkeypatch):
    """When FUN_TIME_MUTE_AUDIO=1 and hide_windows=False, VLC instances must
    still defer playlist loading to prevent audio-leak races.  The satellites
    load with enqueue_only=False and get pl_next."""
    monkeypatch.setenv("FUN_TIME_MUTE_AUDIO", "1")
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    result_file = tmp_path / "core_apps.ini"

    class FakeProc:
        _counter = 0

        def __init__(self, *_args, **_kwargs):
            FakeProc._counter += 1
            self.pid = FakeProc._counter * 100

    FakeProc._counter = 0
    http_commands: list[tuple[int, str]] = []
    replace_calls: list[tuple[int, str, dict]] = []

    def tracking_vlc_http_cmd(port, cmd, pw):
        http_commands.append((port, cmd))
        return True

    def tracking_replace_playlist(port, pw, playlist_path, **kwargs):
        replace_calls.append((port, str(playlist_path), kwargs))
        return True

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", side_effect=lambda *a, **kw: FakeProc()) as popen, \
         patch("fun_time.windows_bridge_startup.wait_for_http", return_value=True), \
         patch("fun_time.windows_bridge_startup.set_repeat_mode", return_value=True), \
         patch("fun_time.windows_bridge_startup.vlc_http_cmd", side_effect=tracking_vlc_http_cmd), \
         patch("fun_time.windows_bridge_startup.replace_playlist_from_file", side_effect=tracking_replace_playlist), \
         patch("fun_time.windows_bridge_startup.time.sleep"):
        launch_core_apps(
            project_dir=tmp_path,
            vlc_exe="vlc.exe",
            portrait_playlist=tmp_path / "state" / "portrait_vlc_playlist.m3u",
            landscape_playlist=tmp_path / "state" / "landscape_vlc_playlist.m3u",
            portrait_port=8091,
            landscape_port=8092,
            password="pw",
            result_file=result_file,
            hide_windows=False,
        )

    # VLC commands must NOT contain .m3u playlist paths (deferred)
    for call in popen.call_args_list:
        cmd = call.args[0] if call.args else call.kwargs.get("args", [])
        if isinstance(cmd, list) and cmd and cmd[0] == "vlc.exe":
            assert not any(arg.endswith(".m3u") for arg in cmd), \
                f"Playlist must not be on VLC command line when muting: {cmd}"

    # Each VLC should get volume mute
    mute_cmds = [(port, cmd) for port, cmd in http_commands if cmd == "volume&val=0"]
    assert len(mute_cmds) == 2, f"Expected 2 mute commands, got {mute_cmds}"

    # Satellites load via HTTP with enqueue_only=False so playback can start.
    assert len(replace_calls) == 2, f"Expected 2 playlist loads, got {replace_calls}"
    enqueue_by_port = {port: kwargs.get("enqueue_only") for port, _, kwargs in replace_calls}
    assert enqueue_by_port == {8091: False, 8092: False}

    # pl_next goes to both satellites.
    next_cmds = [(port, cmd) for port, cmd in http_commands if cmd == "pl_next"]
    assert next_cmds == [(8091, "pl_next"), (8092, "pl_next")]


def test_build_vlc_launch_command_includes_volume_zero_when_mute_env_set(monkeypatch):
    monkeypatch.setenv("FUN_TIME_MUTE_AUDIO", "1")
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="repeat")
    idx = cmd.index("--volume")
    assert cmd[idx + 1] == "0"
    assert "--repeat" in cmd


def test_build_vlc_launch_command_omits_volume_when_mute_env_unset(monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="loop")
    assert "--volume" not in cmd


def test_build_vlc_launch_command_never_includes_no_video(monkeypatch):
    """--no-video changes VLC's playback behavior (e.g. repeat-one mode
    enters 'stopped' instead of 'playing' after navigation). Integration
    tests must run with real video output to match production behavior."""
    monkeypatch.setenv("FUN_TIME_RUN_INTEGRATION", "1")
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="repeat")
    assert "--no-video" not in cmd


def test_build_vlc_launch_command_includes_volume_zero_when_mute_for_loading(monkeypatch):
    """VLC must start pre-muted during loading (hide_windows=True) so no
    audio blips before the HTTP mute command arrives."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="repeat", mute=True)
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
            cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode, mute=mute)
            assert "--start-paused" not in cmd, \
                f"--start-paused must never appear (repeat_mode={repeat_mode}, mute={mute})"


def test_build_vlc_launch_command_never_includes_random(monkeypatch):
    """--random must never appear: it causes VLC to re-pick a random item on every
    navigation, making pl_play&id=N index arithmetic wrong. The playlist builder
    shuffles the sources instead."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode)
        assert "--random" not in cmd, f"--random must not appear (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_includes_no_random(monkeypatch):
    """--no-random must always appear to override VLC's saved config (vlcrc).
    Without it, if the user ever manually enabled shuffle in VLC, the setting
    persists and VLC advances randomly instead of sequentially, breaking
    prev/next navigation which relies on sequential playlist order."""
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode)
        assert "--no-random" in cmd, f"--no-random must appear to override saved vlcrc (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_appends_playlist_path_when_given(tmp_path, monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    playlist_path = tmp_path / "test.m3u"
    cmd = _build_vlc_launch_command(
        "vlc.exe", 8090, "pw", repeat_mode="loop", playlist_path=playlist_path,
    )
    assert cmd[-1] == str(playlist_path)


def test_build_vlc_launch_command_omits_playlist_when_not_given(monkeypatch):
    monkeypatch.delenv("FUN_TIME_MUTE_AUDIO", raising=False)
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="loop")
    assert not any(arg.endswith(".m3u") for arg in cmd)
    # Without a playlist the command ends at the mode flags.
    assert cmd[-1] == "--loop"
