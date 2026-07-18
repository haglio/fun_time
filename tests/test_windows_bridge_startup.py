from __future__ import annotations

import configparser
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

from fun_time.audio_volume import MAX_VOLUME, read_volume
from fun_time.modes import FModePlaylistPlan
from fun_time.modes import SatelliteLibraryContext
from fun_time.window_layout import WindowRect
from fun_time.windows_bridge_startup import (
    _build_satellite_launch_command,
    launch_satellite,
    ensure_broker,
    launch_core_apps,
    launch_genau,
    launch_nau,
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    reap_orphaned_satellites,
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


def test_reap_orphaned_satellites_is_scoped_to_the_satellite_module():
    """A crash or unclean close can strand the two satellite players; a second
    session then has four players racing two command/status file sets.  The
    startup reap clears them — scoped by command line to ``-m <satellite_module>``
    so it can never reach Nau (``-m nau``), the orchestrator, or a path that merely
    contains the word — and it never throws."""
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        reap_orphaned_satellites("satellite")

    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[0] == "powershell.exe"
    ps_command = argv[-1]
    assert "-m\\s+satellite" in ps_command
    assert "Get-CimInstance Win32_Process" in ps_command
    assert "Stop-Process" in ps_command
    assert run.call_args.kwargs.get("check") is False


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
        portrait_playlist_path=state_dir / "portrait_playlist.tsv",
        landscape_playlist_path=state_dir / "landscape_playlist.tsv",
        nau_playlist_path=state_dir / "nau_playlist.tsv",
    )


def test_start_core_session_runs_broker_seed_playlists_and_core_launch(tmp_path: Path):
    result_file = tmp_path / "core_session.ini"
    state_dir = tmp_path / "state"
    plan = _fake_playlist_plan(state_dir)
    portrait_rect = WindowRect(x=2560, y=0, width=1440, height=2500)
    landscape_rect = WindowRect(x=1664, y=0, width=896, height=1392)

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites") as reap, patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ) as ensure, patch(
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
            genau_python_exe="genau_python.exe",
            satellite_module="satellite",
            portrait_cmd_file=state_dir / "portrait_cmd.txt",
            portrait_paused_file=state_dir / "portrait_paused.txt",
            portrait_status_file=state_dir / "portrait_status.txt",
            landscape_cmd_file=state_dir / "landscape_cmd.txt",
            landscape_paused_file=state_dir / "landscape_paused.txt",
            landscape_status_file=state_dir / "landscape_status.txt",
            portrait_rect=portrait_rect,
            landscape_rect=landscape_rect,
            primary_sources="primary_a|primary_b",
            portrait_sources="portrait_a",
            landscape_sources="landscape_a",
            favs_file=tmp_path / "favs.csv",
            state_dir=state_dir,
            result_file=result_file,
            provider_media_root=tmp_path / "media",
            provider_metadata_root=tmp_path / "metadata",
        )

    # A satellite pair stranded by a prior crash is reaped before the new one launches.
    reap.assert_called_once_with("satellite")
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
    # The two native satellites are launched with the genau python, the shared
    # satellite module, the builder's playlists, and each side's file quartet.
    launch.assert_called_once_with(
        python_exe="genau_python.exe",
        satellite_module="satellite",
        portrait_playlist=plan.portrait_playlist_path,
        landscape_playlist=plan.landscape_playlist_path,
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        portrait_rect=portrait_rect,
        landscape_rect=landscape_rect,
        result_file=result_file,
    )


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


def test_launch_core_apps_spawns_two_native_satellites_and_writes_result(tmp_path: Path):
    """launch_core_apps spawns exactly two native satellites — portrait then
    landscape — each with its own playlist and command/paused/status quartet, and
    records both pids in the result INI.  The primary VLC is gone and there is no
    HTTP interface to wait on: the native player owns its playlist and plays at
    once, so nothing is enqueued, repeat-set, or waited for here."""
    result_file = tmp_path / "core_apps.ini"
    state_dir = tmp_path / "state"
    portrait_playlist = state_dir / "portrait_playlist.tsv"
    landscape_playlist = state_dir / "landscape_playlist.tsv"
    portrait_rect = WindowRect(x=2560, y=0, width=1440, height=2500)
    landscape_rect = WindowRect(x=1664, y=0, width=896, height=1392)

    with patch(
        "fun_time.windows_bridge_startup.launch_satellite", side_effect=[202, 303]
    ) as launch_satellite_mock:
        launch_core_apps(
            python_exe="genau_python.exe",
            satellite_module="satellite",
            portrait_playlist=portrait_playlist,
            landscape_playlist=landscape_playlist,
            portrait_cmd_file=state_dir / "portrait_cmd.txt",
            portrait_paused_file=state_dir / "portrait_paused.txt",
            portrait_status_file=state_dir / "portrait_status.txt",
            landscape_cmd_file=state_dir / "landscape_cmd.txt",
            landscape_paused_file=state_dir / "landscape_paused.txt",
            landscape_status_file=state_dir / "landscape_status.txt",
            portrait_rect=portrait_rect,
            landscape_rect=landscape_rect,
            result_file=result_file,
        )

    # Exactly two native satellites: portrait first, then landscape.
    assert launch_satellite_mock.call_count == 2
    portrait_kwargs = launch_satellite_mock.call_args_list[0].kwargs
    landscape_kwargs = launch_satellite_mock.call_args_list[1].kwargs

    # Each satellite gets the genau python, the shared module, its own playlist,
    # and its own command/paused/status quartet.  Each also gets a DISTINCT title
    # so the sequencer can resolve each window to its slot by caption when the pid
    # lookup fails — the portrait title on the portrait side, never swapped.
    assert portrait_kwargs["python_exe"] == "genau_python.exe"
    assert portrait_kwargs["satellite_module"] == "satellite"
    assert portrait_kwargs["title"] == "Satellite Portrait"
    assert portrait_kwargs["playlist_file"] == portrait_playlist
    assert portrait_kwargs["command_file"] == state_dir / "portrait_cmd.txt"
    assert portrait_kwargs["paused_file"] == state_dir / "portrait_paused.txt"
    assert portrait_kwargs["status_file"] == state_dir / "portrait_status.txt"

    assert landscape_kwargs["python_exe"] == "genau_python.exe"
    assert landscape_kwargs["satellite_module"] == "satellite"
    assert landscape_kwargs["title"] == "Satellite Landscape"
    assert landscape_kwargs["playlist_file"] == landscape_playlist
    assert landscape_kwargs["command_file"] == state_dir / "landscape_cmd.txt"
    assert landscape_kwargs["paused_file"] == state_dir / "landscape_paused.txt"
    assert landscape_kwargs["status_file"] == state_dir / "landscape_status.txt"

    # Each satellite launches straight into its own real rect (mpv won't rescale
    # on a later Win32 resize), so the portrait rect must land on the portrait
    # satellite and the landscape rect on the landscape one — never swapped.
    assert (
        portrait_kwargs["x"], portrait_kwargs["y"],
        portrait_kwargs["width"], portrait_kwargs["height"],
    ) == (portrait_rect.x, portrait_rect.y, portrait_rect.width, portrait_rect.height)
    assert (
        landscape_kwargs["x"], landscape_kwargs["y"],
        landscape_kwargs["width"], landscape_kwargs["height"],
    ) == (landscape_rect.x, landscape_rect.y, landscape_rect.width, landscape_rect.height)

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("result", "portrait_pid") == "202"
    assert parser.get("result", "landscape_pid") == "303"
    assert set(parser["result"].keys()) == {"portrait_pid", "landscape_pid"}


def test_build_satellite_launch_command_forwards_the_file_quartet_and_geometry():
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Satellite Portrait",
        playlist_file="state/portrait_playlist.tsv",
        command_file="state/portrait_cmd.txt",
        paused_file="state/portrait_paused.txt",
        status_file="state/portrait_status.txt",
        x=2560, y=0, width=1440, height=2500,
    )
    assert cmd[:3] == ["python.exe", "-m", "satellite"]

    def _val(flag):
        return cmd[cmd.index(flag) + 1]

    assert _val("--title") == "Satellite Portrait"
    assert _val("--playlist") == "state/portrait_playlist.tsv"
    assert _val("--command-file") == "state/portrait_cmd.txt"
    assert _val("--paused-file") == "state/portrait_paused.txt"
    assert _val("--status-file") == "state/portrait_status.txt"
    assert (_val("--x"), _val("--y"), _val("--width"), _val("--height")) == ("2560", "0", "1440", "2500")


def test_build_satellite_launch_command_forwards_the_distinct_title():
    # The two satellites carry distinct captions; the sequencer resolves each
    # window to its slot by title when the pid lookup fails, so a shared caption
    # (or a dropped --title) would let the portrait/landscape windows cross.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Satellite Landscape",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert cmd[cmd.index("--title") + 1] == "Satellite Landscape"


def test_build_satellite_launch_command_always_disables_audio():
    # A satellite must never be heard; unlike VLC there is no shared Windows
    # mixer to worry about, but the clip's own audio track must still be dropped.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Satellite Portrait",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert "--no-audio" in cmd


def test_build_satellite_launch_command_passes_no_config_flag():
    # The satellite CLI takes no --config (unlike Nau); it is fully specified by
    # the file quartet and geometry, so none must be forwarded.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Satellite Portrait",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert "--config" not in cmd


def test_launch_satellite_starts_process_and_returns_pid():
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(51)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        pid = launch_satellite(
            python_exe="python.exe",
            satellite_module="satellite",
            title="Satellite Portrait",
            playlist_file="state/portrait_playlist.tsv",
            command_file="state/portrait_cmd.txt",
            paused_file="state/portrait_paused.txt",
            status_file="state/portrait_status.txt",
            x=2560,
            y=0,
            width=1440,
            height=2500,
        )

    assert pid == 51
    assert popen.call_args.kwargs == {"creationflags": 1}
    assert popen.call_args.args[0][:3] == ["python.exe", "-m", "satellite"]
    assert popen.call_args.args[0][-1] == "--no-audio"
    argv = popen.call_args.args[0]
    assert argv[argv.index("--title") + 1] == "Satellite Portrait"
