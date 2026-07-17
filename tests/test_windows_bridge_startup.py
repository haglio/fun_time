from __future__ import annotations

import configparser
import json
from pathlib import Path
from unittest.mock import ANY, patch
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from fun_time.audio_volume import MAX_VOLUME, read_volume
from fun_time.modes import FModePlaylistPlan
from fun_time.modes import SatelliteLibraryContext
from fun_time.windows_bridge_startup import (
    _VLC_HTTP_BIND_TIMEOUT_MS,
    _await_vlc_http,
    _build_satellite_launch_command,
    _build_vlc_launch_command,
    ensure_broker,
    launch_core_apps,
    launch_genau,
    launch_nau,
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    restart_broker,
    seed_startup_states,
    start_core_session,
)


def test_restart_broker_stops_and_launches_tray(tmp_path: Path):
    """The tray launches with the broker's own kwargs, not the ordinary
    hidden-window ones: it has to break away from an integration run's job
    object and outlive the run."""
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    launcher.parent.mkdir()
    launcher.touch()

    with patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop, \
         patch("fun_time.windows_bridge_startup.time.sleep") as sleep, \
         patch("fun_time.windows_bridge_startup.subprocess.Popen") as popen, \
         patch("fun_time.windows_bridge_startup.broker_launch_kwargs", return_value={"creationflags": 1}):
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


def test_ensure_broker_leaves_a_live_broker_alone(tmp_path: Path):
    """A fresh heartbeat means a healthy broker is already running — a previous
    session's, or the one osr2_broker's self-healing task keeps up.  Startup must
    not kill it: harem and the user's direct VLC+MFP use keep talking to it, and
    tearing it down would drop every client mid-stream."""
    heartbeat = tmp_path / "broker_heartbeat.txt"
    with patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=True) as fresh, \
         patch("fun_time.windows_bridge_startup.restart_broker") as restart:
        ensure_broker(tmp_path, heartbeat, tmp_path / "launch_broker_tray.vbs")

    fresh.assert_called_once_with(heartbeat)
    restart.assert_not_called()


def test_ensure_broker_restarts_a_dead_broker(tmp_path: Path):
    """A stale or missing heartbeat means no broker is up, so start one —
    restart_broker clears any zombie first, then relaunches the tray."""
    heartbeat = tmp_path / "broker_heartbeat.txt"
    launcher = tmp_path / "launch_broker_tray.vbs"
    with patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=False), \
         patch("fun_time.windows_bridge_startup.restart_broker") as restart:
        ensure_broker(tmp_path, heartbeat, launcher)

    restart.assert_called_once_with(tmp_path, launcher)


def _rfb_config(cfg_factory, tmp_path: Path, *, lazy_load: bool) -> Path:
    user_data_dir = tmp_path / "User Data"
    user_data_dir.mkdir()
    (user_data_dir / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "Blair"}}}}),
        encoding="utf-8",
    )
    favs = tmp_path / "favs.csv"
    favs.write_text(
        "local_file,web_url\r\n"
        '"","=HYPERLINK(""https://example.com/image/abc"";""https://example.com/image/abc"")"\r\n',
        encoding="utf-8",
    )
    return cfg_factory(
        {
            "paths": {"favs_file": str(favs)},
            "random_favs_browser": {
                "enabled": True,
                "user_data_dir": str(user_data_dir),
                "open_count": 10,
                "lazy_load": lazy_load,
            },
        }
    )


def test_prepare_random_favs_browser_manifest_defers_each_tab_behind_a_local_page(
    cfg_factory, tmp_path: Path
):
    """With lazy_load the manifest lists local pages, each naming its fav."""
    cfg_path = _rfb_config(cfg_factory, tmp_path, lazy_load=True)
    output_path = tmp_path / "browser_manifest.txt"

    prepare_random_favs_browser_manifest(cfg_path, output_path)

    profile, tab_uri = output_path.read_text(encoding="utf-8").splitlines()
    assert profile == "Profile 2"
    assert tab_uri.startswith("file:///")
    page = Path(url2pathname(urlparse(tab_uri).path)).read_text(encoding="utf-8")
    assert "https://example.com/image/abc" in page


def test_prepare_random_favs_browser_manifest_lists_urls_directly_without_lazy_load(
    cfg_factory, tmp_path: Path
):
    cfg_path = _rfb_config(cfg_factory, tmp_path, lazy_load=False)
    output_path = tmp_path / "browser_manifest.txt"

    prepare_random_favs_browser_manifest(cfg_path, output_path)

    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "Profile 2",
        "https://example.com/image/abc",
    ]


def test_seed_startup_states_writes_all_three_pause_flags(tmp_path: Path):
    genau_file = tmp_path / "genau_paused.txt"
    audio_file = tmp_path / "audio_paused.txt"
    nau_file = tmp_path / "nau_paused.txt"

    seed_startup_states(genau_file, audio_file, nau_file, tmp_path / "audio_volume.txt")

    # Genau parked, audio parked, Nau paused until the sequencer's reveal.
    assert genau_file.read_text(encoding="utf-8") == "1"
    assert audio_file.read_text(encoding="utf-8") == "1"
    assert nau_file.read_text(encoding="utf-8") == "1"


def test_seed_startup_states_restores_full_volume(tmp_path: Path):
    """A session muted last night must not come back silent: Nau and the audio
    companion both launch at full volume, so the published level must say so."""
    volume_file = tmp_path / "audio_volume.txt"
    volume_file.write_text("0", encoding="utf-8")

    seed_startup_states(
        tmp_path / "genau_paused.txt",
        tmp_path / "audio_paused.txt",
        tmp_path / "nau_paused.txt",
        volume_file,
    )

    assert read_volume(volume_file) == MAX_VOLUME


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

    with patch("fun_time.windows_bridge_startup.ensure_broker") as ensure, patch(
        "fun_time.windows_bridge_startup.seed_startup_states"
    ) as seed, patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ) as prepare, patch(
        "fun_time.windows_bridge_startup.build_fmode_playlists", return_value=plan
    ) as build, patch("fun_time.windows_bridge_startup.launch_core_apps") as launch:
        start_core_session(
            project_dir=tmp_path,
            config_path="fun_time_config.json",
            broker_heartbeat_file=state_dir / "broker_heartbeat.txt",
            random_favs_browser_manifest_file=tmp_path / "browser_manifest.txt",
            genau_paused_file=tmp_path / "genau_paused.txt",
            audio_paused_file=tmp_path / "audio_paused.txt",
            nau_paused_file=tmp_path / "nau_paused.txt",
            audio_volume_file=tmp_path / "audio_volume.txt",
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

    # Startup leaves a live broker alone, only (re)starting a dead one.
    ensure.assert_called_once_with(tmp_path, state_dir / "broker_heartbeat.txt", None)
    seed.assert_called_once_with(
        tmp_path / "genau_paused.txt",
        tmp_path / "audio_paused.txt",
        tmp_path / "nau_paused.txt",
        tmp_path / "audio_volume.txt",
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

    with patch("fun_time.windows_bridge_startup.ensure_broker"), patch(
        "fun_time.windows_bridge_startup.seed_startup_states"
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
            audio_volume_file=tmp_path / "v.txt",
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


def test_launch_nau_forwards_metadata_dir_when_given(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(7)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_nau(
            python_exe="python.exe", nau_module="nau", config_path="cfg.json",
            playlist_file="pl.tsv", command_file="cmd", paused_file="paused",
            status_file="status", nau_x=0, nau_y=0, nau_width=100, nau_height=100,
            metadata_dir="C:/videos/metadata",
        )

    command = popen.call_args.args[0]
    assert "--metadata-dir" in command
    assert command[command.index("--metadata-dir") + 1] == "C:/videos/metadata"


def test_launch_nau_omits_metadata_dir_when_absent(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(7)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_nau(
            python_exe="python.exe", nau_module="nau", config_path="cfg.json",
            playlist_file="pl.tsv", command_file="cmd", paused_file="paused",
            status_file="status", nau_x=0, nau_y=0, nau_width=100, nau_height=100,
        )

    assert "--metadata-dir" not in popen.call_args.args[0]


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


class _FakeProc:
    def __init__(self, pid: int):
        self.pid = pid


_DASHBOARD_COMMAND = [
    "python.exe", "-m", "fun_time.dashboard_app", "windows_bridge_launch.ini",
    "--x", "10", "--y", "20", "--width", "30", "--height", "40",
    "--rfb-x", "5", "--rfb-y", "44", "--rfb-width", "31", "--rfb-height", "96",
]
_AUDIO_COMMAND = [
    "python.exe", "-m", "fun_time.audio_companion_app",
    "--config", "cfg.json", "--audio-folder", "audio",
]
_LOCK_HUD_COMMAND = ["python.exe", "-m", "fun_time.lock_hud_app", "windows_bridge_launch.ini"]


def _call_launch_ui_companions(result_file, *, dashboard_enabled, hud_enabled):
    launch_ui_companions(
        python_exe="python.exe",
        dashboard_module="fun_time.dashboard_app",
        dashboard_enabled=dashboard_enabled,
        lock_hud_module="fun_time.lock_hud_app",
        hud_enabled=hud_enabled,
        windows_bridge_manifest_path="windows_bridge_launch.ini",
        dashboard_x=10, dashboard_y=20, dashboard_width=30, dashboard_height=40,
        rfb_x=5, rfb_y=44, rfb_width=31, rfb_height=96,
        audio_module="fun_time.audio_companion_app",
        config_path="cfg.json",
        audio_folder="audio",
        result_file=result_file,
    )


def _ui_result(result_file):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    return parser["result"]


def test_launch_ui_companions_launches_dashboard_hud_and_audio(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    with patch(
        "fun_time.windows_bridge_startup.subprocess.Popen",
        side_effect=[_FakeProc(11), _FakeProc(22), _FakeProc(33)],
    ) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        _call_launch_ui_companions(result_file, dashboard_enabled=True, hud_enabled=True)

    assert popen.call_count == 3
    assert popen.call_args_list[0].args[0] == _DASHBOARD_COMMAND
    assert popen.call_args_list[1].args[0] == _LOCK_HUD_COMMAND
    assert popen.call_args_list[2].args[0] == _AUDIO_COMMAND

    result = _ui_result(result_file)
    assert result["dashboard_pid"] == "11"
    assert result["lock_hud_pid"] == "22"
    assert result["audio_pid"] == "33"
    assert set(result.keys()) == {"dashboard_pid", "lock_hud_pid", "audio_pid"}


def test_launch_ui_companions_skips_hud_when_disabled(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    with patch(
        "fun_time.windows_bridge_startup.subprocess.Popen",
        side_effect=[_FakeProc(11), _FakeProc(33)],
    ) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        _call_launch_ui_companions(result_file, dashboard_enabled=True, hud_enabled=False)

    assert popen.call_count == 2
    assert popen.call_args_list[0].args[0] == _DASHBOARD_COMMAND
    assert popen.call_args_list[1].args[0] == _AUDIO_COMMAND

    result = _ui_result(result_file)
    assert result["dashboard_pid"] == "11"
    assert result["lock_hud_pid"] == "0"
    assert result["audio_pid"] == "33"


def test_launch_ui_companions_skips_dashboard_when_disabled(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    with patch(
        "fun_time.windows_bridge_startup.subprocess.Popen",
        side_effect=[_FakeProc(22), _FakeProc(33)],
    ) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        _call_launch_ui_companions(result_file, dashboard_enabled=False, hud_enabled=True)

    assert popen.call_count == 2
    assert popen.call_args_list[0].args[0] == _LOCK_HUD_COMMAND
    assert popen.call_args_list[1].args[0] == _AUDIO_COMMAND

    result = _ui_result(result_file)
    assert result["dashboard_pid"] == "0"
    assert result["lock_hud_pid"] == "22"
    assert result["audio_pid"] == "33"


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


def test_launch_core_apps_defers_playlists_and_keeps_audio_when_hide_windows_true(tmp_path: Path, monkeypatch):
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
    vlc_cmdlines = []
    for call in popen.call_args_list:
        cmd = call.args[0] if call.args else call.kwargs.get("args", [])
        if isinstance(cmd, list) and cmd and cmd[0] == "vlc.exe":
            vlc_cmdlines.append(cmd)
            assert not any(arg.endswith(".m3u") for arg in cmd), \
                f"Playlist must not be on VLC command line when hide_windows=True: {cmd}"

    # Nothing plays yet, and the satellites can never make a sound anyway.
    assert http_commands == [], f"No HTTP commands allowed during loading: {http_commands}"
    for cmd in vlc_cmdlines:
        assert "--no-audio" in cmd, f"A satellite must never be heard: {cmd}"

    # Playlists must be enqueued (not played) via replace_playlist_from_file
    assert len(replace_calls) == 2, f"Expected 2 playlist loads, got {replace_calls}"
    for port, path, kwargs in replace_calls:
        assert path == str(playlists[port])
        assert kwargs.get("enqueue_only") is True, \
            f"enqueue_only must be True to prevent playback during loading: {kwargs}"


def test_build_vlc_launch_command_never_passes_volume():
    """VLC's volume is a Windows per-application mixer level shared by every
    vlc.exe: whatever we set it to survives our process and greets the user
    the next time they open VLC themselves.  Fun Time must never set it.
    (``--volume`` is also dead in VLC 3: "option --volume no longer exists".)
    """
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="repeat")
    assert "--volume" not in cmd


def test_build_vlc_launch_command_always_disables_audio():
    """A satellite must never be heard: a handful of clips carry an audio
    track, and one surfacing mid-session is exactly the surprise the old
    volume-0 mute existed to prevent.  --no-audio skips the audio output
    module entirely, so there is no sound and no audio session — where a
    volume of 0 would have persisted into the user's own VLC."""
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode)
        assert "--no-audio" in cmd, f"satellites must never make a sound ({repeat_mode})"


def test_build_vlc_launch_command_never_includes_no_video():
    """--no-video changes VLC's playback behavior (e.g. repeat-one mode
    enters 'stopped' instead of 'playing' after navigation). Integration
    tests must run with real video output to match production behavior."""
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="repeat")
    assert "--no-video" not in cmd


def test_build_vlc_launch_command_never_includes_start_paused():
    """--start-paused must NEVER be used: VLC re-applies it on every item
    transition, not just startup.  This causes a black screen every time
    the user navigates.  Deferring the playlist keeps loading quiet."""
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode)
        assert "--start-paused" not in cmd, \
            f"--start-paused must never appear (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_never_includes_random():
    """--random must never appear: it causes VLC to re-pick a random item on every
    navigation, making pl_play&id=N index arithmetic wrong. The playlist builder
    shuffles the sources instead."""
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode)
        assert "--random" not in cmd, f"--random must not appear (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_includes_no_random():
    """--no-random must always appear to override VLC's saved config (vlcrc).
    Without it, if the user ever manually enabled shuffle in VLC, the setting
    persists and VLC advances randomly instead of sequentially, breaking
    prev/next navigation which relies on sequential playlist order."""
    for repeat_mode in ("repeat", "loop"):
        cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode=repeat_mode)
        assert "--no-random" in cmd, f"--no-random must appear to override saved vlcrc (repeat_mode={repeat_mode})"


def test_build_vlc_launch_command_appends_playlist_path_when_given(tmp_path):
    playlist_path = tmp_path / "test.m3u"
    cmd = _build_vlc_launch_command(
        "vlc.exe", 8090, "pw", repeat_mode="loop", playlist_path=playlist_path,
    )
    assert cmd[-1] == str(playlist_path)


def test_build_vlc_launch_command_omits_playlist_when_not_given():
    cmd = _build_vlc_launch_command("vlc.exe", 8090, "pw", repeat_mode="loop")
    assert not any(arg.endswith(".m3u") for arg in cmd)


def test_build_satellite_launch_command_forwards_the_file_quartet_and_geometry():
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        playlist_file="state/portrait_playlist.tsv",
        command_file="state/portrait_cmd.txt",
        paused_file="state/portrait_paused.txt",
        status_file="state/portrait_status.txt",
        x=2560, y=0, width=1440, height=2500,
    )
    assert cmd[:3] == ["python.exe", "-m", "satellite"]

    def _val(flag):
        return cmd[cmd.index(flag) + 1]

    assert _val("--playlist") == "state/portrait_playlist.tsv"
    assert _val("--command-file") == "state/portrait_cmd.txt"
    assert _val("--paused-file") == "state/portrait_paused.txt"
    assert _val("--status-file") == "state/portrait_status.txt"
    assert (_val("--x"), _val("--y"), _val("--width"), _val("--height")) == ("2560", "0", "1440", "2500")


def test_build_satellite_launch_command_always_disables_audio():
    # A satellite must never be heard; unlike VLC there is no shared Windows
    # mixer to worry about, but the clip's own audio track must still be dropped.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert "--no-audio" in cmd


def test_build_satellite_launch_command_passes_no_config_flag():
    # The satellite CLI takes no --config (unlike Nau); it is fully specified by
    # the file quartet and geometry, so none must be forwarded.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert "--config" not in cmd
