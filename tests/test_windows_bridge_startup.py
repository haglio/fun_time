from __future__ import annotations

import logging

import configparser
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

from fun_time.audio_volume import MAX_VOLUME, read_volume
from fun_time.broker_control import PARK_CMD
from fun_time.shared_state import BridgeState
from fun_time.modes import SatelliteLibraryContext
from fun_time.shared_state import read_shared_state, shared_state_path, write_shared_state
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.win32_taskbar import APP_USER_MODEL_ID
from fun_time.windows_bridge_startup import (
    TASKBAR_IDENTITY_ARGS,
    _build_satellite_launch_command,
    broker_source_mtime,
    launch_satellite,
    ensure_broker,
    launch_core_apps,
    launch_genau,
    launch_nau,
    launch_origenerator,
    launch_broker_tray,
    launch_ui_companions,
    prepare_random_favs_browser_manifest,
    reap_orphaned_satellites,
    seed_startup_states,
    start_core_session,
    stop_broker_processes,
)


def test_stop_broker_processes_is_a_machine_wide_sweep_with_nothing_to_scope():
    """The kill matches broker and tray processes by command line, across the
    whole machine — there is no directory it is relative to.  Handing it a
    working directory implied a scoping that does not exist and cost every
    caller a path to compute for it."""
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        stop_broker_processes()

    argv = run.call_args.args[0]
    assert argv[0] == "powershell.exe"
    assert "Stop-Process" in argv[-1]
    assert "cwd" not in run.call_args.kwargs


def test_the_sweep_reaches_the_tray_now_that_it_is_a_python_process():
    """osr2_broker's tray became `pythonw -m osr2_broker.tray`.

    Missed by the sweep, it would survive the kill and immediately restart the
    broker we just stopped.
    """
    import re

    from fun_time.orchestrator_broker import BROKER_TRAY_PATTERN

    tray_command_line = (
        r'"C:\path\to\broker\.venv\Scripts\pythonw.exe" '
        r'-m osr2_broker.tray --config '
        r'C:\path\to\broker\osr2_broker_config.json'
    )
    assert re.search(BROKER_TRAY_PATTERN, tray_command_line)

    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        stop_broker_processes()

    ps_command = run.call_args.args[0][-1]
    python_clause = ps_command.split("-or")[0]
    assert BROKER_TRAY_PATTERN in python_clause


def test_launch_broker_tray_uses_the_brokers_own_launch_kwargs(tmp_path: Path):
    """The tray launches with the broker's own kwargs, not the ordinary
    hidden-window ones: it has to break away from an integration run's job
    object and outlive the run."""
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    launcher.parent.mkdir()
    launcher.touch()

    with patch("fun_time.windows_bridge_startup.subprocess.Popen") as popen, \
         patch("fun_time.windows_bridge_startup.broker_launch_kwargs", return_value={"creationflags": 1}):
        launch_broker_tray(launcher)

    popen.assert_called_once_with(
        ["wscript.exe", str(launcher)], cwd=launcher.parent, creationflags=1,
    )


def test_launch_broker_tray_skips_launch_when_no_launcher():
    with patch("fun_time.windows_bridge_startup.subprocess.Popen") as popen:
        launch_broker_tray(None)

    popen.assert_not_called()


def test_ensure_broker_does_not_kill_on_a_merely_stale_heartbeat(tmp_path: Path):
    """A stale heartbeat is not proof of a dead broker.  osr2_broker stops
    ticking it whenever it cannot hold the serial port, so a powered-off OSR2
    makes a healthy broker look gone — and that is exactly when a session starts.
    Launch over it and let the single-instance mutexes make it a no-op, rather
    than killing what we cannot see."""
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    launcher.parent.mkdir()
    launcher.touch()
    heartbeat = tmp_path / "broker_heartbeat.txt"

    with patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=False), \
         patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop, \
         patch("fun_time.windows_bridge_startup.subprocess.Popen") as popen, \
         patch("fun_time.windows_bridge_startup.broker_launch_kwargs", return_value={}):
        ensure_broker(heartbeat, launcher)

    stop.assert_not_called()
    popen.assert_called_once_with(["wscript.exe", str(launcher)], cwd=launcher.parent)


def test_ensure_broker_restarts_a_broker_older_than_its_own_code(tmp_path: Path):
    """The one case that earns a kill: the running broker predates the code it
    should be running, so fun_time's vocabulary is ahead of what it understands
    and any newer verb is dropped without a word.  That is a fact about two
    timestamps, unlike the stale heartbeat above, which is only a guess — so it
    overrides even a perfectly fresh heartbeat.
    """
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    launcher.parent.mkdir()
    launcher.touch()
    heartbeat = tmp_path / "broker_heartbeat.txt"

    with patch("fun_time.windows_bridge_startup.broker_source_mtime", return_value=2000.0), \
         patch("fun_time.windows_bridge_startup.broker_process_started_at", return_value=1000.0), \
         patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=True), \
         patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop, \
         patch("fun_time.windows_bridge_startup.launch_broker_tray") as launch:
        ensure_broker(heartbeat, launcher)

    stop.assert_called_once()
    launch.assert_called_once_with(launcher)


def test_ensure_broker_does_not_kill_a_broker_newer_than_its_code(tmp_path: Path):
    """The usual case, and the one that must never kill: the running broker was
    started after the last time its sources changed, so it understands everything
    we can say to it."""
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    launcher.parent.mkdir()
    launcher.touch()

    with patch("fun_time.windows_bridge_startup.broker_source_mtime", return_value=1000.0), \
         patch("fun_time.windows_bridge_startup.broker_process_started_at", return_value=2000.0), \
         patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=True), \
         patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop:
        ensure_broker(tmp_path / "broker_heartbeat.txt", launcher)

    stop.assert_not_called()


def test_ensure_broker_does_not_ask_about_the_process_when_it_cannot_read_the_code(
    tmp_path: Path,
):
    """No readable broker package means no answer, and no answer is not permission
    to kill.  It also fixes the order: the directory read comes first, so an
    ordinary startup never spawns a PowerShell process to ask a question the
    cheap half has already settled."""
    with patch("fun_time.windows_bridge_startup.broker_process_started_at") as started, \
         patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=True), \
         patch("fun_time.windows_bridge_startup.stop_broker_processes") as stop:
        ensure_broker(tmp_path / "broker_heartbeat.txt", tmp_path / "nowhere" / "launch.vbs")

    started.assert_not_called()
    stop.assert_not_called()


def test_broker_source_mtime_is_the_newest_python_file_in_the_package(tmp_path: Path):
    """What the running process actually loaded is the package's .py files, so a
    log or config written beside them must not read as a code change — that would
    restart the broker on every startup."""
    launcher = tmp_path / "osr2_broker" / "launch_broker_tray.vbs"
    package = launcher.parent / "osr2_broker"
    package.mkdir(parents=True)
    (package / "session.py").touch()
    os.utime(package / "session.py", (1000.0, 1000.0))
    (package / "app.py").touch()
    os.utime(package / "app.py", (3000.0, 3000.0))
    (package / "osr2_broker.log").touch()
    os.utime(package / "osr2_broker.log", (9000.0, 9000.0))

    assert broker_source_mtime(launcher) == 3000.0


def test_a_missing_tray_launcher_says_so_rather_than_going_quiet(tmp_path: Path, caplog):
    """The one thing the second, deleted broker policy said that this one did
    not: a session whose config names no tray launcher (or names a file that is
    not there) gets no broker, and used to get no line about it either — which
    is a silent OSR2 with nothing anywhere to explain it."""
    with caplog.at_level(logging.WARNING, logger="fun_time.windows_bridge_startup"):
        launch_broker_tray(tmp_path / "nowhere" / "launch_broker_tray.vbs")

    assert any("broker" in record.getMessage().lower() for record in caplog.records)


def test_ensure_broker_leaves_a_live_broker_alone(tmp_path: Path):
    """A fresh heartbeat means a healthy broker is already running — a previous
    session's, or the one osr2_broker's self-healing task keeps up.  With nothing
    saying its code has moved on, startup leaves it be: the user's own tools keep
    talking to it, and tearing it down would drop every client mid-stream."""
    heartbeat = tmp_path / "broker_heartbeat.txt"
    with patch("fun_time.windows_bridge_startup.is_broker_heartbeat_fresh", return_value=True) as fresh, \
         patch("fun_time.windows_bridge_startup.launch_broker_tray") as launch:
        ensure_broker(heartbeat, tmp_path / "launch_broker_tray.vbs")

    fresh.assert_called_once_with(heartbeat)
    launch.assert_not_called()


def test_reap_orphaned_satellites_is_scoped_to_the_satellite_module():
    """A crash or unclean close can strand the two satellite players; a second
    session then has four players racing two command/status file sets.  The
    startup reap clears them — scoped by command line to ``-m <satellite_module>``
    so it can never reach Nau (``-m nau``), the orchestrator, or a path that merely
    contains the word — and it never throws."""
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        reap_orphaned_satellites("satellite", ["C:/state/portrait_status.txt"])

    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[0] == "powershell.exe"
    ps_command = argv[-1]
    assert "-m\\s+satellite" in ps_command
    assert "Get-CimInstance Win32_Process" in ps_command
    assert "Stop-Process" in ps_command
    assert run.call_args.kwargs.get("check") is False


def test_reap_orphaned_satellites_only_reaches_players_holding_our_own_state_files():
    """The reap must not be able to leave its own session.

    Every satellite on the machine runs ``-m satellite``, so a module-only sweep
    killed *every* satellite alive — including the two in the user's live session,
    from an integration run whose state dir is somewhere else entirely.  That is
    what took both of the user's players down mid-session, leaving no traceback
    because nothing crashed: they were terminated.

    Scoping to the status files this session is about to take over is exactly the
    reason the reap exists ("a stranded pair keeps reading the same files"), and a
    session that does not own those files cannot match."""
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        reap_orphaned_satellites(
            "satellite",
            [Path(r"C:\state\portrait_status.txt"), Path(r"C:\state\landscape_status.txt")],
        )

    ps_command = run.call_args.args[0][-1]
    assert r"C:\state\portrait_status.txt" in ps_command
    assert r"C:\state\landscape_status.txt" in ps_command
    # The command line has to actually be tested against them, not merely mention
    # them — a bare `-m satellite` match is the machine-wide sweep again.
    assert "CommandLine.Contains" in ps_command


def test_reap_orphaned_satellites_does_nothing_without_state_files_to_claim():
    """No files to take over means no satellite can be stranded on them, so there
    is nothing to reap — and certainly no licence to sweep the machine."""
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run:
        reap_orphaned_satellites("satellite", [])

    run.assert_not_called()


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


def _seed_startup_states(tmp_path: Path, **overrides):
    """seed_startup_states over throwaway paths under *tmp_path*."""
    kwargs = dict(
        genau_paused_file=tmp_path / "genau_paused.txt",
        audio_paused_file=tmp_path / "audio_paused.txt",
        nau_paused_file=tmp_path / "nau_paused.txt",
        audio_volume_file=tmp_path / "audio_volume.txt",
        genau_cmd_file=tmp_path / "genau_cmd.txt",
        nau_cmd_file=tmp_path / "nau_cmd.txt",
    )
    kwargs.update(overrides)
    return seed_startup_states(
        kwargs.pop("genau_paused_file"), kwargs.pop("audio_paused_file"),
        kwargs.pop("nau_paused_file"), kwargs.pop("audio_volume_file"),
        kwargs.pop("genau_cmd_file"), **kwargs,
    )


def test_seed_startup_states_writes_all_three_pause_flags(tmp_path: Path):
    genau_file = tmp_path / "genau_paused.txt"
    audio_file = tmp_path / "audio_paused.txt"
    nau_file = tmp_path / "nau_paused.txt"

    _seed_startup_states(tmp_path)

    # Genau parked, audio parked, Nau paused until the sequencer's reveal.
    assert genau_file.read_text(encoding="utf-8") == "1"
    assert audio_file.read_text(encoding="utf-8") == "1"
    assert nau_file.read_text(encoding="utf-8") == "1"


def test_seed_startup_states_blanks_genaus_display(tmp_path: Path):
    """Genau blanks on DISPLAY_OFF and defaults to owning its display, so a
    session that starts in nau mode — no mode switch, nothing to transition —
    has to say so, or Genau comes up painting its clips over Nau's window."""
    genau_cmd = tmp_path / "genau_cmd.txt"

    _seed_startup_states(tmp_path, genau_cmd_file=genau_cmd)

    assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
        "PAUSE", "DISPLAY_OFF", "SET_VOLUME 100 0",
    ]


def _nau_verbs(tmp_path: Path) -> list[str]:
    """What is queued for Nau, one verb per line as it drains them."""
    return (tmp_path / "nau_cmd.txt").read_text(encoding="utf-8").split("\n")[:-1]


def test_seed_startup_states_hands_the_primary_slot_to_genau_for_a_genau_session(tmp_path: Path):
    """A session left showing Genau has to come back showing Genau, and every
    verb a live switch would have written has to be written here too — the
    session is *built* in nau mode, so opening in another one IS that switch,
    seeded before either player launches instead of sent to a running one."""
    genau_cmd = tmp_path / "genau_cmd.txt"

    _seed_startup_states(tmp_path, genau_cmd_file=genau_cmd, mode="genau")

    assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
        # The nau-mode reset leads; the replayed switch's verbs queue behind it
        # and the drain applies them in order, so the last of each kind wins —
        # the switch's RESUME taken back by the hold that follows it (see
        # test_seed_startup_states_holds_genau_off_the_osr2_for_the_reveal).
        "PAUSE", "DISPLAY_OFF",
        "RESUME", "DISPLAY_ON", "PAUSE", "SET_VOLUME 100 0",
    ]
    assert _nau_verbs(tmp_path) == [
        "SET_HYBRID 0", "DISPLAY_OFF", "SET_VOLUME 100 0", "SET_F_MODE 0",
    ]


def test_seed_startup_states_holds_every_player_for_the_reveal(tmp_path: Path):
    """Whatever mode is coming back, nothing plays until the loading screen is
    gone.  The switch this replays would have started Genau outright — right for
    a live switch, wrong here, where it would drive the OSR2 for the twenty
    seconds the user spends watching a progress bar."""
    for mode in ("nau", "genau", "hybrid"):
        _seed_startup_states(tmp_path, mode=mode)

        assert (tmp_path / "genau_paused.txt").read_text(encoding="utf-8") == "1", mode
        assert (tmp_path / "audio_paused.txt").read_text(encoding="utf-8") == "1", mode
        assert (tmp_path / "nau_paused.txt").read_text(encoding="utf-8") == "1", mode


def _genau_play_verb(tmp_path: Path) -> str | None:
    """The last PAUSE/RESUME queued for Genau — the one its drain leaves it on."""
    verbs = [
        verb for verb in (tmp_path / "genau_cmd.txt").read_text(encoding="utf-8").splitlines()
        if verb in ("PAUSE", "RESUME")
    ]
    return verbs[-1] if verbs else None


def test_seed_startup_states_holds_genau_off_the_osr2_for_the_reveal(tmp_path: Path):
    """The flags above do not reach Genau, so the hold has to be said on its own
    channel as well.  Under Fun Time Genau runs in direct control, where the
    paused flag is never read and the stroke follows PAUSE/RESUME here — so the
    switch's RESUME was still queued when Genau finished loading, and a session
    resuming into genau or hybrid drove the OSR2 behind the loading screen."""
    for mode in ("nau", "genau", "hybrid"):
        _seed_startup_states(tmp_path, mode=mode)

        assert _genau_play_verb(tmp_path) == "PAUSE", mode


def test_seed_startup_states_puts_genaus_hud_up_for_a_hybrid_session(tmp_path: Path):
    """Hybrid is both players at once: Genau's transparent HUD over Nau's video,
    which each of them has to be told about."""
    genau_cmd = tmp_path / "genau_cmd.txt"

    _seed_startup_states(tmp_path, genau_cmd_file=genau_cmd, mode="hybrid")

    assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
        "PAUSE", "DISPLAY_OFF",
        "RESUME", "HUD_ON", "DISPLAY_ON", "PAUSE", "SET_VOLUME 100 0",
    ]
    assert _nau_verbs(tmp_path)[:2] == ["SET_HYBRID 1", "DISPLAY_ON"]


def test_seed_startup_states_opens_a_fresh_session_at_full_volume(tmp_path: Path):
    """With nothing asked for, both sinks come up unattenuated and unnarrowed —
    what a first run, and every session before either was remembered, opens on."""
    volume_file = tmp_path / "audio_volume.txt"
    volume_file.write_text("0", encoding="utf-8")

    _seed_startup_states(tmp_path, audio_volume_file=volume_file)

    assert read_volume(volume_file) == MAX_VOLUME
    assert _nau_verbs(tmp_path) == ["SET_VOLUME 100 0", "SET_F_MODE 0"]


def test_seed_startup_states_seeds_the_level_the_session_was_left_at(tmp_path: Path):
    """Nau and the audio companion each launch unattenuated and neither reads a
    level it already has, so seeding is the only way a resumed session comes up
    as loud as it was left."""
    volume_file = tmp_path / "audio_volume.txt"

    _seed_startup_states(tmp_path, audio_volume_file=volume_file, volume=40)

    assert read_volume(volume_file) == 40
    assert _nau_verbs(tmp_path)[0] == "SET_VOLUME 40 0"


def test_seed_startup_states_tells_genau_the_level_too(tmp_path: Path):
    """Genau draws the primary display's volume chip in the mode it owns the
    screen, so it is told the level like Nau is — in every mode, not only its own,
    or the chip it draws is wrong for as long as it takes the first "quieter"."""
    genau_cmd = tmp_path / "genau_cmd.txt"

    _seed_startup_states(tmp_path, genau_cmd_file=genau_cmd, volume=40, muted=True)

    verbs = genau_cmd.read_text(encoding="utf-8").splitlines()
    assert verbs[-1] == "SET_VOLUME 40 1"
    assert verbs[-1] == _nau_verbs(tmp_path)[-2], "the two players are told the same"


def test_seed_startup_states_seeds_a_mute_as_silence_and_as_a_mute(tmp_path: Path):
    """The companion is only asked to be quiet, so a mute reaches it as zero;
    Nau also draws the control, so it gets the level and the flag and can say
    muted rather than turned all the way down."""
    volume_file = tmp_path / "audio_volume.txt"

    _seed_startup_states(tmp_path, audio_volume_file=volume_file, volume=40, muted=True)

    assert read_volume(volume_file) == 0
    assert _nau_verbs(tmp_path)[0] == "SET_VOLUME 40 1"


def test_seed_startup_states_tells_nau_whether_f_mode_is_on(tmp_path: Path):
    """The playlist Nau is handed has already been narrowed and a list of
    scripted videos looks like any other, so its HUD can only know from being
    told — and it is told alongside the level, not instead of it: both verbs
    have to survive on a channel nothing has drained yet."""
    _seed_startup_states(tmp_path, f_mode=True)

    assert _nau_verbs(tmp_path) == ["SET_VOLUME 100 0", "SET_F_MODE 1"]


def _start_core_session_kwargs(tmp_path: Path) -> dict:
    """The full keyword argument set for a start_core_session call.

    Shared so the several start_core_session tests do not each re-spell the ~26
    orchestration paths; each test binds what it needs from the returned dict.
    """
    state_dir = tmp_path / "state"
    return dict(
        config_path="fun_time_config.json",
        broker_cmd_file=state_dir / "broker_cmd.txt",
        broker_heartbeat_file=state_dir / "broker_heartbeat.txt",
        random_favs_browser_manifest_file=tmp_path / "browser_manifest.txt",
        genau_paused_file=tmp_path / "genau_paused.txt",
        genau_cmd_file=tmp_path / "genau_cmd.txt",
        audio_paused_file=tmp_path / "audio_paused.txt",
        nau_paused_file=tmp_path / "nau_paused.txt",
        audio_volume_file=tmp_path / "audio_volume.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        satellite_python_exe="fun_time_python.exe",
        satellite_module="satellite",
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        nau_status_file=state_dir / "nau_status.txt",
        portrait_log_file=state_dir / "portrait_satellite.log",
        landscape_log_file=state_dir / "landscape_satellite.log",
        portrait_rect=WindowRect(x=2560, y=0, width=1440, height=2500),
        landscape_rect=WindowRect(x=1664, y=0, width=896, height=1392),
        main_sources=f"{tmp_path / 'main_a'}|{tmp_path / 'main_b'}",
        portrait_sources=str(tmp_path / "portrait_a"),
        landscape_sources=str(tmp_path / "landscape_a"),
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        result_file=tmp_path / "core_session.ini",
        regen_metadata_root=tmp_path / "metadata",
    )


def test_start_core_session_runs_broker_seed_playlists_and_core_launch(tmp_path: Path):
    """The first-run path: the state dir holds no playlists to resume, so the
    session is built from scratch and launched."""
    kwargs = _start_core_session_kwargs(tmp_path)
    state_dir = kwargs["state_dir"]
    result_file = kwargs["result_file"]
    portrait_rect = kwargs["portrait_rect"]
    landscape_rect = kwargs["landscape_rect"]

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites") as reap, patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ) as ensure, patch(
        "fun_time.windows_bridge_startup.seed_startup_states"
    ) as seed, patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ) as prepare, patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ) as build, patch("fun_time.windows_bridge_startup.launch_core_apps") as launch:
        start_core_session(**kwargs)

    # A satellite pair stranded by a prior crash on THIS session's own status
    # files is reaped before the new one launches — and nothing beyond them, so a
    # session running elsewhere on the machine survives our startup.
    reap.assert_called_once_with(
        "satellite",
        [state_dir / "portrait_status.txt", state_dir / "landscape_status.txt"],
    )
    # Startup leaves a live broker alone, only starting one when none answers.
    ensure.assert_called_once_with(state_dir / "broker_heartbeat.txt", None)
    # Seeded at what this session opens on — full volume, F-mode off, on Nau,
    # with no session to come back to.
    seed.assert_called_once_with(
        tmp_path / "genau_paused.txt",
        tmp_path / "audio_paused.txt",
        tmp_path / "nau_paused.txt",
        tmp_path / "audio_volume.txt",
        tmp_path / "genau_cmd.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        volume=MAX_VOLUME,
        muted=False,
        f_mode=False,
        mode="nau",
    )
    prepare.assert_called_once_with("fun_time_config.json", tmp_path / "browser_manifest.txt")
    # Every player's playlist, each built with its F-mode off — the flags default
    # off, which is what a session with nothing to resume opens in.
    build.assert_called_once_with(
        main_sources=kwargs["main_sources"],
        portrait_sources=kwargs["portrait_sources"],
        landscape_sources=kwargs["landscape_sources"],
        favs_file=tmp_path / "favs.csv",
        state_dir=state_dir,
        library=SatelliteLibraryContext(
            metadata_root=tmp_path / "metadata",
            watch_stats_file=state_dir / "watch_stats.json",
        ),
    )
    # The two native satellites are launched with OUR python (the player ships
    # from this repo), the satellite module, the builder's playlists, and each
    # side's file quartet.
    launch.assert_called_once_with(
        python_exe="fun_time_python.exe",
        satellite_module="satellite",
        portrait_playlist=state_dir / "portrait_playlist.tsv",
        landscape_playlist=state_dir / "landscape_playlist.tsv",
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        # Each side's stdout+stderr go to its own log, so a satellite that dies of
        # an unhandled exception leaves the traceback on disk.
        portrait_log_file=state_dir / "portrait_satellite.log",
        landscape_log_file=state_dir / "landscape_satellite.log",
        portrait_rect=portrait_rect,
        landscape_rect=landscape_rect,
        result_file=result_file,
        # Each satellite also draws its own lock HUD, so it is told which panel
        # file to render and where to post the clicks on it.
        portrait_hud_file=None,
        landscape_hud_file=None,
        dashboard_cmd_file=None,
        # And which sibling checkouts to run out of — the satellites import
        # player_core, so the named checkouts reach them like Genau and Nau.
        project_dirs=None,
    )


def _seed_resumable_session(kwargs: dict) -> dict[str, list[str]]:
    """Last session's three playlist files and status files, on disk.

    Each player gets two clips drawn from the very dirs this session's source
    spec names — the shape a real build leaves behind — and its status file
    names the second, so a resume rotates that one to the front.
    """
    state_dir = kwargs["state_dir"]
    state_dir.mkdir(parents=True, exist_ok=True)
    sources = {
        "portrait": kwargs["portrait_sources"],
        "landscape": kwargs["landscape_sources"],
        "nau": kwargs["main_sources"].split("|")[0],
    }
    left_on = {}
    for name, source_dir in sources.items():
        Path(source_dir).mkdir(parents=True, exist_ok=True)
        clips = []
        for index in (1, 2):
            clip = Path(source_dir) / f"{name} scene {index}.mp4"
            clip.write_bytes(b"")
            clips.append(str(clip))
        left_on[name] = clips
        (state_dir / f"{name}_playlist.tsv").write_text(
            "".join(f"{c}\n" for c in clips), encoding="utf-8"
        )
        (state_dir / f"{name}_status.txt").write_text(f"video={clips[1]}\n", encoding="utf-8")
    return left_on


def test_start_core_session_resumes_last_session_rather_than_reshuffling(tmp_path: Path, caplog):
    """Reopening Fun Time lands on the clips it was closed on: with last
    session's playlist files still on disk, each is rotated onto the video that
    player published and no fresh shuffle is built over them."""
    kwargs = _start_core_session_kwargs(tmp_path)
    state_dir = kwargs["state_dir"]
    left_on = _seed_resumable_session(kwargs)

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites"), patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ), patch("fun_time.windows_bridge_startup.seed_startup_states"), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ) as build, patch("fun_time.windows_bridge_startup.launch_core_apps"):
        with caplog.at_level("INFO", logger="fun_time.windows_bridge_startup"):
            start_core_session(**kwargs)

    build.assert_not_called()
    for name, (first, second) in left_on.items():
        playlist = state_dir / f"{name}_playlist.tsv"
        assert playlist.read_text(encoding="utf-8").splitlines() == [second, first]
    # Which clips you get is the whole difference between the two paths, so the
    # session says which one it took.
    assert "Resumed last session's playlists" in caplog.text


def _run_start_core_session(kwargs: dict) -> str:
    """start_core_session with what reaches outside the state dir patched away.

    The seeding is left real — it only writes flags under the state dir, and it
    is what puts a resumed session's sound level on both audio sinks.
    """
    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites"), patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ), patch("fun_time.windows_bridge_startup.launch_core_apps"):
        return start_core_session(**kwargs)


def test_start_core_session_opens_the_primary_slot_in_the_mode_it_was_left_in(tmp_path: Path):
    """Which player owns the big display is a thing you set, so leaving the
    session on Genau and reopening on Nau is an overnight reset like any other.
    The mode is also handed back to the caller, because the windows have to be
    parked to match it and only the sequencer holds their handles."""
    kwargs = _start_core_session_kwargs(tmp_path)
    _seed_resumable_session(kwargs)
    write_shared_state(
        shared_state_path(kwargs["state_dir"]), BridgeState(main_mode="genau")
    )

    assert _run_start_core_session(kwargs) == "genau"

    # Genau is told to come back up painting; the reveal is what then lets it
    # drive, so the switch's RESUME is taken back here and both players are held.
    assert kwargs["genau_cmd_file"].read_text(encoding="utf-8").splitlines() == [
        "PAUSE", "DISPLAY_OFF",
        "RESUME", "DISPLAY_ON", "PAUSE", "SET_VOLUME 100 0",
    ]
    assert kwargs["nau_paused_file"].read_text(encoding="utf-8") == "1"


def test_start_core_session_puts_the_primary_back_in_the_loop_it_was_running(tmp_path: Path):
    """A loop is a range inside one video, held in the mpv process that just
    died, so it is re-sent the way a satellite's lock is — waiting in Nau's
    command file before Nau launches, over the video the resume put at the top
    of the main player's playlist."""
    kwargs = _start_core_session_kwargs(tmp_path)
    left_on = _seed_resumable_session(kwargs)
    (kwargs["state_dir"] / "nau_status.txt").write_text(
        f"video={left_on['nau'][1]}\nstate=looping\nloop_in_ms=2000\nloop_out_ms=4000\n",
        encoding="utf-8",
    )

    _run_start_core_session(kwargs)

    assert "SET_LOOP 2000 4000" in kwargs["nau_cmd_file"].read_text(
        encoding="utf-8"
    ).splitlines()


def test_start_core_session_drops_a_loop_whose_video_did_not_come_back(tmp_path: Path):
    """The clip the loop was cut from was deleted since, so the rotation had
    nothing to land on and some other video leads.  Sending the bounds anyway
    would loop three seconds of a video the user never marked."""
    kwargs = _start_core_session_kwargs(tmp_path)
    _seed_resumable_session(kwargs)
    (kwargs["state_dir"] / "nau_status.txt").write_text(
        f"video={tmp_path / 'deleted.mp4'}\nstate=looping\nloop_in_ms=2000\nloop_out_ms=4000\n",
        encoding="utf-8",
    )

    _run_start_core_session(kwargs)

    assert "SET_LOOP" not in kwargs["nau_cmd_file"].read_text(encoding="utf-8")


def test_start_core_session_opens_a_fresh_session_on_nau(tmp_path: Path):
    """Nothing to resume means no mode to come back to, and the main slot's
    own default is Nau — the same one every session is built in."""
    kwargs = _start_core_session_kwargs(tmp_path)

    assert _run_start_core_session(kwargs) == "nau"


def test_start_core_session_reopens_in_the_mode_the_resumed_playlists_were_built_in(
    tmp_path: Path,
):
    """The playlists that just came back were built under last session's F-mode,
    filter, order and loop, and the dispatch loop opens on the state file — so
    wiping it left the session playing favorites while every HUD said F-mode
    was off, and the next "F-mode" then reported it *enabled* to no visible
    effect.  What shaped the files on disk comes back with them."""
    kwargs = _start_core_session_kwargs(tmp_path)
    _seed_resumable_session(kwargs)
    state_file = shared_state_path(kwargs["state_dir"])
    write_shared_state(state_file, BridgeState(
        main_f_mode=True,
        portrait_f_mode=True,
        portrait_filter="alpha",
        landscape_latest=True,
        portrait_loop="seed",
        portrait_map_anchor="C:/v/a.mp4",
    ))

    _run_start_core_session(kwargs)

    state = read_shared_state(state_file)
    assert state is not None
    assert (state.main_f_mode, state.portrait_f_mode) == (True, True)
    assert state.portrait_filter == "alpha"
    assert state.landscape_latest is True
    assert state.portrait_loop == "seed"
    assert state.portrait_map_anchor == "C:/v/a.mp4"
    # fun_time draws the satellites' HUD model and the dashboard's off that
    # state, but Nau's own HUD can only know F-mode from being told — so it is
    # told, or the main player is the one display that comes back saying nothing.
    assert "SET_F_MODE 1" in kwargs["nau_cmd_file"].read_text(encoding="utf-8").splitlines()


def test_start_core_session_comes_up_at_the_sound_level_it_was_left_at(tmp_path: Path):
    """The level lives in the bridge, not in anything the players read on their
    own, so it reaches this session only by being seeded — to the audio
    companion as the audible level, and to Nau as the level plus the mute it
    draws over it."""
    kwargs = _start_core_session_kwargs(tmp_path)
    _seed_resumable_session(kwargs)
    write_shared_state(
        shared_state_path(kwargs["state_dir"]), BridgeState(volume=40, muted=True)
    )

    _run_start_core_session(kwargs)

    state = read_shared_state(shared_state_path(kwargs["state_dir"]))
    assert (state.volume, state.muted) == (40, True)
    assert read_volume(kwargs["audio_volume_file"]) == 0
    assert kwargs["nau_cmd_file"].read_text(encoding="utf-8").splitlines()[0] == (
        "SET_VOLUME 40 1"
    )


def test_start_core_session_relocks_the_satellite_that_was_locked(tmp_path: Path):
    """A lock dies with the mpv process holding it, so it is queued back to the
    side that had one — waiting in the command file before that satellite
    launches, and drained on its first tick over the clip the resume put at the
    top of its playlist."""
    kwargs = _start_core_session_kwargs(tmp_path)
    _seed_resumable_session(kwargs)
    write_shared_state(
        shared_state_path(kwargs["state_dir"]), BridgeState(locked2=True, locked3=False)
    )

    _run_start_core_session(kwargs)

    state = read_shared_state(shared_state_path(kwargs["state_dir"]))
    assert (state.locked2, state.locked3) == (True, False)
    assert kwargs["portrait_cmd_file"].read_text(encoding="utf-8").split() == ["LOCK"]
    assert not kwargs["landscape_cmd_file"].exists()


def test_start_core_session_opens_a_freshly_built_session_on_a_clean_state(tmp_path: Path):
    """Nothing to resume: the builder wrote three new playlists with F-mode off,
    so last session's state describes files that no longer exist.  This is also
    what clears an OmniPause a crash left stranded."""
    kwargs = _start_core_session_kwargs(tmp_path)
    state_file = shared_state_path(kwargs["state_dir"])
    write_shared_state(state_file, BridgeState(main_f_mode=True, omni_paused=True))

    _run_start_core_session(kwargs)

    assert read_shared_state(state_file) == BridgeState()


def test_start_core_session_rebuilds_the_primary_under_the_resumed_f_mode(tmp_path: Path):
    """The rebuild for another app's playlist has to honor the F-mode the
    satellites came back in — the main player's own reading of it, funscripted
    clips only — or one player quietly holds the whole library while the HUDs
    say F-mode.  The order it came back in rides along for the same reason: the
    state carried forward has to describe the file this writes."""
    kwargs = _start_core_session_kwargs(tmp_path)
    left_on = _seed_resumable_session(kwargs)
    state_dir = kwargs["state_dir"]
    vr_clip = tmp_path / "vr_library" / "headset scene.mp4"
    vr_clip.parent.mkdir(parents=True, exist_ok=True)
    vr_clip.write_bytes(b"")
    (state_dir / "nau_playlist.tsv").write_text(
        f"{vr_clip}\n{left_on['nau'][0]}\n", encoding="utf-8"
    )
    write_shared_state(
        shared_state_path(state_dir), BridgeState(main_f_mode=True, main_latest=True)
    )

    with patch("fun_time.windows_bridge_startup.build_main_playlist") as rebuild:
        _run_start_core_session(kwargs)

    rebuild.assert_called_once_with(
        state_dir / "nau_playlist.tsv", kwargs["main_sources"], f_mode=True, recent=True
    )


def test_start_core_session_rebuilds_a_primary_playlist_left_by_another_app(
    tmp_path: Path, caplog
):
    """FunTimeVR writes its main playlist to the very file the desktop
    session resumes from, and builds it from the VR library merged with this
    one's — so reopening the desktop app inherited VR videos it must never
    play.  Only the main player is rebuilt: both apps build the satellites from the
    same dirs, so their resume is still last session's and is kept."""
    kwargs = _start_core_session_kwargs(tmp_path)
    state_dir = kwargs["state_dir"]
    left_on = _seed_resumable_session(kwargs)
    # The other app's addition: a video from a library this session never names.
    vr_clip = tmp_path / "vr_library" / "headset scene.mp4"
    vr_clip.parent.mkdir(parents=True, exist_ok=True)
    vr_clip.write_bytes(b"")
    nau_playlist = state_dir / "nau_playlist.tsv"
    nau_playlist.write_text(
        f"{vr_clip}\n{left_on['nau'][0]}\n", encoding="utf-8"
    )
    (state_dir / "nau_status.txt").write_text(f"video={vr_clip}\n", encoding="utf-8")

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites"), patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ), patch("fun_time.windows_bridge_startup.seed_startup_states"), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ) as build, patch("fun_time.windows_bridge_startup.launch_core_apps"):
        with caplog.at_level("INFO", logger="fun_time.windows_bridge_startup"):
            start_core_session(**kwargs)

    # The main player comes back from this session's own library, and the foreign
    # video is gone from it.
    rebuilt = nau_playlist.read_text(encoding="utf-8").splitlines()
    assert sorted(line.split("\t")[0] for line in rebuilt) == sorted(left_on["nau"])
    # The satellites keep the resume; nothing rebuilt all three.
    build.assert_not_called()
    for name in ("portrait", "landscape"):
        first, second = left_on[name]
        playlist = state_dir / f"{name}_playlist.tsv"
        assert playlist.read_text(encoding="utf-8").splitlines() == [second, first]
    assert "rebuilt the main player's" in caplog.text


def test_start_core_session_clears_stale_satellite_paused_flags(tmp_path: Path):
    """A prior session's OmniPause can strand "1" in the satellite paused files;
    seed_startup_states does not touch them and nothing else clears them, so a
    fresh session's satellites would read paused and never play (frozen at 0).
    start_core_session must reset both to "0" before the satellites launch."""
    kwargs = _start_core_session_kwargs(tmp_path)
    portrait_paused = kwargs["portrait_paused_file"]
    landscape_paused = kwargs["landscape_paused_file"]
    portrait_paused.parent.mkdir(parents=True, exist_ok=True)
    portrait_paused.write_text("1", encoding="utf-8")  # stranded by a prior OmniPause
    landscape_paused.write_text("1", encoding="utf-8")

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites"), patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ), patch("fun_time.windows_bridge_startup.seed_startup_states"), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ), patch("fun_time.windows_bridge_startup.launch_core_apps"):
        start_core_session(**kwargs)

    assert portrait_paused.read_text(encoding="utf-8") == "0"
    assert landscape_paused.read_text(encoding="utf-8") == "0"


def test_a_session_resumed_into_origenerator_mode_seeds_its_players_paused(tmp_path: Path):
    """The regions are the hosted app's for the whole of origenerator mode, so a
    session that closed in it comes back with both players paused (and black,
    off the published mode) — exactly as the mode switch would have left them,
    rather than playing invisibly under the restored app."""
    from fun_time.shared_state import BridgeState
    from fun_time.shared_state import shared_state_path, write_shared_state

    kwargs = _start_core_session_kwargs(tmp_path)
    write_shared_state(shared_state_path(kwargs["state_dir"]),
                       BridgeState(satellites_mode="origenerator"))

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites"), patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ), patch("fun_time.windows_bridge_startup.seed_startup_states"), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ), patch("fun_time.windows_bridge_startup.launch_core_apps"), patch(
        "fun_time.windows_bridge_startup.resume_playlists", return_value=True
    ):
        start_core_session(**kwargs)

    assert kwargs["portrait_paused_file"].read_text(encoding="utf-8") == "1"
    assert kwargs["landscape_paused_file"].read_text(encoding="utf-8") == "1"


def test_start_core_session_parks_the_osr2_before_the_startup_wait(tmp_path: Path):
    """Opening Fun Time sends the OSR2 home before anything slow begins.

    Startup runs long — two native players decode their first frames while Nau
    and Genau scan their libraries — and wherever the last session left the
    device is where it would sit for all of it.  So the park is queued at the
    very top, ahead of the launches that make the wait.  The broker reads its
    command file on a tick and nothing clears it at broker startup, so the verb
    keeps whether the broker is already up or is still coming up behind
    ``ensure_broker``.
    """
    kwargs = _start_core_session_kwargs(tmp_path)
    broker_cmd_file = kwargs["broker_cmd_file"]

    with patch("fun_time.windows_bridge_startup.reap_orphaned_satellites"), patch(
        "fun_time.windows_bridge_startup.ensure_broker"
    ), patch("fun_time.windows_bridge_startup.seed_startup_states"), patch(
        "fun_time.windows_bridge_startup.prepare_random_favs_browser_manifest"
    ), patch(
        "fun_time.windows_bridge_startup.build_all_playlists"
    ), patch("fun_time.windows_bridge_startup.launch_core_apps"):
        start_core_session(**kwargs)

    assert broker_cmd_file.read_text(encoding="utf-8") == PARK_CMD


def test_launch_genau_starts_process_and_returns_pid():
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


def test_launch_genau_forwards_command_and_paused_files():
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
            drive_file="state/genau_drive.txt",
        )

    assert pid == 42
    command = popen.call_args.args[0]
    assert "--command-file" in command
    idx = command.index("--command-file")
    assert command[idx + 1] == "state/genau_cmd.txt"
    assert "--paused-file" in command
    idx = command.index("--paused-file")
    assert command[idx + 1] == "state/genau_paused.txt"
    # Where Genau publishes the readout Nau draws in Hybrid.  Named by us, because
    # Genau resolving it from its own config put it in a directory Nau never read.
    assert "--drive-file" in command
    idx = command.index("--drive-file")
    assert command[idx + 1] == "state/genau_drive.txt"


def test_launch_genau_opens_on_the_clip_it_was_left_showing():
    """Genau rescans its clips folder every launch and starts at the top of it,
    so the clip a session was left on comes back only by being named — on the
    command line, since it has to be in hand before the first clip decodes."""
    class FakeProc:
        pid = 42

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_genau(
            python_exe="python.exe", genau_module="genau", config_path="cfg.json",
            clips_folder="clips", genau_x=0, genau_y=0, genau_width=1, genau_height=1,
            start_clip="C:/clips/alpha.mp4",
        )

    command = popen.call_args.args[0]
    assert command[command.index("--start-clip") + 1] == "C:/clips/alpha.mp4"


def test_launch_genau_names_no_clip_for_a_session_with_none_to_resume():
    """A first run, or a Genau that published nothing: the flag is left off
    rather than passed empty, so Genau opens where its own scan starts."""
    class FakeProc:
        pid = 42

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_genau(
            python_exe="python.exe", genau_module="genau", config_path="cfg.json",
            clips_folder="clips", genau_x=0, genau_y=0, genau_width=1, genau_height=1,
            start_clip="",
        )

    assert "--start-clip" not in popen.call_args.args[0]


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
            status_file="status", console_file="console.json",
            drive_file="drive.txt", dashboard_cmd_file="dash_cmd.txt",
            log_file=tmp_path / "nau.log",
            nau_x=0, nau_y=0, nau_width=100, nau_height=100,
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
            status_file="status", console_file="console.json",
            drive_file="drive.txt", dashboard_cmd_file="dash_cmd.txt",
            log_file=tmp_path / "nau.log",
            nau_x=0, nau_y=0, nau_width=100, nau_height=100,
        )

    assert "--metadata-dir" not in popen.call_args.args[0]


def test_launch_nau_makes_it_borderless(tmp_path: Path):
    """Under Fun Time Nau drops its title bar, like the satellites; standalone it
    keeps its chrome, so the flag has to come from the launcher, not be Nau's
    default."""
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(7)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_nau(
            python_exe="python.exe", nau_module="nau", config_path="cfg.json",
            playlist_file="pl.tsv", command_file="cmd", paused_file="paused",
            status_file="status", console_file="console.json",
            drive_file="drive.txt", dashboard_cmd_file="dash_cmd.txt",
            log_file=tmp_path / "nau.log",
            nau_x=0, nau_y=0, nau_width=100, nau_height=100,
        )

    assert "--borderless" in popen.call_args.args[0]


def test_launch_genau_passes_fun_time_flag():
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


class TestEveryPlayerWearsFunTimesTaskbarIdentity:
    """One launch, one button.  Windows groups by AppUserModelID and takes the
    icon from the pinned shortcut carrying the same one, so a player that claims
    its own puts a second application on the bar, and one that claims none lands
    under whatever the shared interpreter's path is registered to — which was some
    unrelated program's mark.  Fun Time tells each of them instead."""

    class _FakeProc:
        def __init__(self, pid: int = 1):
            self.pid = pid

    def _launched(self, launch, **kwargs) -> list[str]:
        with patch("fun_time.windows_bridge_startup.subprocess.Popen",
                   return_value=self._FakeProc()) as popen, \
             patch("fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}):
            launch(**kwargs)
        return popen.call_args.args[0]

    @staticmethod
    def _identity(command: list[str]) -> str | None:
        if "--taskbar-identity" not in command:
            return None
        return command[command.index("--taskbar-identity") + 1]

    def test_the_satellites_are_told_who_they_belong_to(self):
        command = _build_satellite_launch_command(
            "python.exe", "satellite", title="Portrait AI Player",
            playlist_file="pl.tsv", command_file="cmd", paused_file="paused",
            status_file="status", x=0, y=0, width=100, height=100,
        )

        assert self._identity(command) == APP_USER_MODEL_ID

    def test_nau_is_told_who_it_belongs_to(self, tmp_path: Path):
        command = self._launched(
            launch_nau,
            python_exe="python.exe", nau_module="nau", config_path="cfg.json",
            playlist_file="pl.tsv", command_file="cmd", paused_file="paused",
            status_file="status", console_file="console.json",
            drive_file="drive.txt", dashboard_cmd_file="dash_cmd.txt",
            log_file=tmp_path / "nau.log",
            nau_x=0, nau_y=0, nau_width=100, nau_height=100,
        )

        assert self._identity(command) == APP_USER_MODEL_ID

    def test_genau_is_told_who_it_belongs_to(self):
        command = self._launched(
            launch_genau,
            python_exe="python.exe", genau_module="genau.app", config_path="cfg.json",
            clips_folder="clips", genau_x=0, genau_y=0, genau_width=800, genau_height=600,
        )

        assert self._identity(command) == APP_USER_MODEL_ID

    def test_it_is_the_identity_the_pinned_shortcut_is_stamped_with(self):
        """The icon and the name come off that shortcut, so a second spelling here
        would group these windows under a button with no icon at all."""
        assert TASKBAR_IDENTITY_ARGS == ("--taskbar-identity", APP_USER_MODEL_ID)


class TestGenauCheckout:
    """Which checkouts Genau and Nau are run out of.

    Every package they import — their own, and ``player_core`` under them —
    resolves through the genau venv's editable installs, which name the primary
    checkout of each repo for good.  Naming a directory puts it on their
    ``PYTHONPATH``, ahead of site-packages, so a worktree of either can be run;
    without it a branch could only be judged by landing it first.
    """

    class _Proc:
        pid = 42

    def _popen(self):
        return patch("fun_time.windows_bridge_startup.subprocess.Popen",
                     return_value=self._Proc())

    @staticmethod
    def _genau(**overrides):
        return dict(python_exe="python.exe", genau_module="genau",
                    config_path="cfg.json", clips_folder="clips",
                    genau_x=0, genau_y=0, genau_width=800, genau_height=600,
                    **overrides)

    @staticmethod
    def _nau(tmp_path: Path, **overrides):
        return dict(python_exe="python.exe", nau_module="nau", config_path="cfg.json",
                    playlist_file="pl.tsv", command_file="cmd", paused_file="paused",
                    status_file="status", console_file="console.json",
                    drive_file="drive.txt", dashboard_cmd_file="dash_cmd.txt",
                    log_file=tmp_path / "nau.log",
                    nau_x=0, nau_y=0, nau_width=100, nau_height=100, **overrides)

    @staticmethod
    def _path(popen) -> list[str]:
        return popen.call_args.kwargs["env"]["PYTHONPATH"].split(os.pathsep)

    def test_a_named_checkout_goes_ahead_of_what_the_venv_installed(self, tmp_path: Path):
        checkout = tmp_path / "worktree"
        checkout.mkdir()

        with self._popen() as popen, patch(
                "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}):
            launch_genau(**self._genau(project_dirs=str(checkout)))

        assert self._path(popen)[0] == str(checkout)

    def test_nau_follows_genau_onto_the_same_checkouts(self, tmp_path: Path):
        """Nau ships in that repo too, so it must not stay on the primary while
        its housemate moves — the two would be running different code."""
        checkout = tmp_path / "worktree"
        checkout.mkdir()

        with self._popen() as popen, patch(
                "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}):
            launch_nau(**self._nau(tmp_path, project_dirs=str(checkout)))

        assert self._path(popen)[0] == str(checkout)

    def test_several_checkouts_are_run_against_each_other(self, tmp_path: Path):
        """A change is often in two of these repos at once — a HUD in ../genau on
        a channel in ../player_core — and running one branch against the other's
        landed code is not running the change."""
        genau, core = tmp_path / "genau", tmp_path / "player_core"
        genau.mkdir()
        core.mkdir()

        with self._popen() as popen, patch(
                "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}):
            launch_genau(**self._genau(
                project_dirs=os.pathsep.join([str(genau), str(core)])))

        assert self._path(popen)[:2] == [str(genau), str(core)]

    def test_naming_none_leaves_them_to_their_venv(self):
        """What every session did before this, and what an ordinary one still
        does: nothing said about the path, so the editable installs answer."""
        with self._popen() as popen, patch(
                "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}):
            launch_genau(**self._genau())

        assert "env" not in popen.call_args.kwargs

    def test_a_checkout_that_is_gone_is_dropped_rather_than_fatal(self, tmp_path: Path):
        """A worktree named in the config outlives the worktree itself — a
        session must still start rather than die on its way up."""
        with self._popen() as popen, patch(
                "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}):
            launch_genau(**self._genau(project_dirs=str(tmp_path / "removed")))

        assert "env" not in popen.call_args.kwargs


def test_launch_nau_sends_child_output_to_its_own_log(tmp_path: Path):
    """Nau is the satellites' twin — the same mpv player under the same windowed
    ``pythonw`` — so it needs the same place to leave a traceback when it dies."""
    class FakeProc:
        pid = 43

    log_file = tmp_path / "nau.log"
    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_nau(
            python_exe="pythonw.exe",
            nau_module="nau",
            config_path="cfg.json",
            playlist_file="state/nau_playlist.tsv",
            command_file="state/nau_cmd.txt",
            paused_file="state/nau_paused.txt",
            status_file="state/nau_status.txt",
            console_file="state/nau_console.json",
            drive_file="state/genau_drive.txt",
            dashboard_cmd_file="state/dashboard_cmd.txt",
            log_file=log_file,
            nau_x=100, nau_y=200, nau_width=300, nau_height=400,
        )

    stream = popen.call_args.kwargs["stdout"]
    assert popen.call_args.kwargs["stderr"] is stream
    assert Path(stream.name) == log_file
    assert stream.closed
    assert "-m nau" in log_file.read_text(encoding="utf-8")


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
            console_file="state/nau_console.json",
            drive_file="state/genau_drive.txt",
            dashboard_cmd_file="state/dashboard_cmd.txt",
            log_file=tmp_path / "nau.log",
            nau_x=100,
            nau_y=200,
            nau_width=300,
            nau_height=400,
        )

    assert pid == 43
    assert popen.call_args.kwargs["creationflags"] == 1
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
        "--console-file",
        "state/nau_console.json",
        "--drive-file",
        "state/genau_drive.txt",
        "--dashboard-cmd-file",
        "state/dashboard_cmd.txt",
        "--x",
        "100",
        "--y",
        "200",
        "--width",
        "300",
        "--height",
        "400",
        "--borderless",
        *TASKBAR_IDENTITY_ARGS,
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
def _call_launch_ui_companions(result_file, *, dashboard_enabled):
    launch_ui_companions(
        python_exe="python.exe",
        dashboard_module="fun_time.dashboard_app",
        dashboard_enabled=dashboard_enabled,
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


def test_launch_ui_companions_launches_dashboard_and_audio(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    with patch(
        "fun_time.windows_bridge_startup.subprocess.Popen",
        side_effect=[_FakeProc(11), _FakeProc(33)],
    ) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        _call_launch_ui_companions(result_file, dashboard_enabled=True)

    assert popen.call_count == 2
    assert popen.call_args_list[0].args[0] == _DASHBOARD_COMMAND
    assert popen.call_args_list[1].args[0] == _AUDIO_COMMAND

    result = _ui_result(result_file)
    assert result["dashboard_pid"] == "11"
    assert result["audio_pid"] == "33"
    assert set(result.keys()) == {"dashboard_pid", "audio_pid"}


def test_launch_ui_companions_skips_dashboard_when_disabled(tmp_path: Path):
    result_file = tmp_path / "ui_companions.ini"

    with patch(
        "fun_time.windows_bridge_startup.subprocess.Popen",
        side_effect=[_FakeProc(33)],
    ) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        _call_launch_ui_companions(result_file, dashboard_enabled=False)

    assert popen.call_count == 1
    assert popen.call_args_list[0].args[0] == _AUDIO_COMMAND

    result = _ui_result(result_file)
    assert result["dashboard_pid"] == "0"
    assert result["audio_pid"] == "33"


def test_launch_core_apps_spawns_two_native_satellites_and_writes_result(tmp_path: Path):
    """launch_core_apps spawns exactly two native satellites — portrait then
    landscape — each with its own playlist and command/paused/status quartet, and
    records both pids in the result INI.  The native player owns its playlist and
    plays at once, so nothing is enqueued, repeat-set, or waited for here."""
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
            python_exe="fun_time_python.exe",
            satellite_module="satellite",
            portrait_playlist=portrait_playlist,
            landscape_playlist=landscape_playlist,
            portrait_cmd_file=state_dir / "portrait_cmd.txt",
            portrait_paused_file=state_dir / "portrait_paused.txt",
            portrait_status_file=state_dir / "portrait_status.txt",
            landscape_cmd_file=state_dir / "landscape_cmd.txt",
            landscape_paused_file=state_dir / "landscape_paused.txt",
            landscape_status_file=state_dir / "landscape_status.txt",
            portrait_log_file=state_dir / "portrait_satellite.log",
            landscape_log_file=state_dir / "landscape_satellite.log",
            portrait_rect=portrait_rect,
            landscape_rect=landscape_rect,
            result_file=result_file,
        )

    # Exactly two native satellites: portrait first, then landscape.
    assert launch_satellite_mock.call_count == 2
    portrait_kwargs = launch_satellite_mock.call_args_list[0].kwargs
    landscape_kwargs = launch_satellite_mock.call_args_list[1].kwargs

    # Each satellite gets OUR python, the satellite module, its own playlist,
    # and its own command/paused/status quartet.  Each also gets a DISTINCT title
    # so the sequencer can resolve each window to its slot by caption when the pid
    # lookup fails — the portrait title on the portrait side, never swapped.
    assert portrait_kwargs["python_exe"] == "fun_time_python.exe"
    assert portrait_kwargs["satellite_module"] == "satellite"
    assert portrait_kwargs["title"] == "Portrait AI Player"
    assert portrait_kwargs["playlist_file"] == portrait_playlist
    assert portrait_kwargs["command_file"] == state_dir / "portrait_cmd.txt"
    assert portrait_kwargs["paused_file"] == state_dir / "portrait_paused.txt"
    assert portrait_kwargs["status_file"] == state_dir / "portrait_status.txt"

    assert landscape_kwargs["python_exe"] == "fun_time_python.exe"
    assert landscape_kwargs["satellite_module"] == "satellite"
    assert landscape_kwargs["title"] == "Landscape AI Player"
    assert landscape_kwargs["playlist_file"] == landscape_playlist
    assert landscape_kwargs["command_file"] == state_dir / "landscape_cmd.txt"
    assert landscape_kwargs["paused_file"] == state_dir / "landscape_paused.txt"
    assert landscape_kwargs["status_file"] == state_dir / "landscape_status.txt"

    # Each side's crash log is its own file, so a death on one side is legible
    # without untangling it from the other's output.
    assert portrait_kwargs["log_file"] == state_dir / "portrait_satellite.log"
    assert landscape_kwargs["log_file"] == state_dir / "landscape_satellite.log"

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
        title="Portrait AI Player",
        playlist_file="state/portrait_playlist.tsv",
        command_file="state/portrait_cmd.txt",
        paused_file="state/portrait_paused.txt",
        status_file="state/portrait_status.txt",
        x=2560, y=0, width=1440, height=2500,
    )
    assert cmd[:3] == ["python.exe", "-m", "satellite"]

    def _val(flag):
        return cmd[cmd.index(flag) + 1]

    assert _val("--title") == "Portrait AI Player"
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
        title="Landscape AI Player",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert cmd[cmd.index("--title") + 1] == "Landscape AI Player"


def test_build_satellite_launch_command_always_disables_audio():
    # A satellite must never be heard, so the clip's own audio track is dropped
    # at launch rather than mixed down afterwards.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Portrait AI Player",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert "--no-audio" in cmd


def test_build_satellite_launch_command_passes_no_config_flag():
    # The satellite CLI takes no --config (unlike Nau); it is fully specified by
    # the file quartet and geometry, so none must be forwarded.
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Portrait AI Player",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        x=0, y=0, width=1, height=1,
    )
    assert "--config" not in cmd


def test_launch_satellite_starts_process_and_returns_pid(tmp_path: Path):
    class FakeProc:
        def __init__(self, pid: int):
            self.pid = pid

    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc(51)) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={"creationflags": 1}
    ):
        pid = launch_satellite(
            python_exe="python.exe",
            satellite_module="satellite",
            title="Portrait AI Player",
            role="Portrait",
            playlist_file="state/portrait_playlist.tsv",
            command_file="state/portrait_cmd.txt",
            paused_file="state/portrait_paused.txt",
            status_file="state/portrait_status.txt",
            log_file=tmp_path / "portrait_satellite.log",
            x=2560,
            y=0,
            width=1440,
            height=2500,
        )

    assert pid == 51
    assert popen.call_args.kwargs["creationflags"] == 1
    assert popen.call_args.args[0][:3] == ["python.exe", "-m", "satellite"]
    assert "--no-audio" in popen.call_args.args[0]
    argv = popen.call_args.args[0]
    assert argv[argv.index("--title") + 1] == "Portrait AI Player"


def test_launch_satellite_sends_child_output_to_its_own_log(tmp_path: Path):
    """A satellite runs under ``pythonw``, which has no console: with nothing
    attached to its stdout/stderr an unhandled exception kills it leaving no
    trace at all.  Both streams go to one per-side log so a death leaves the
    Python traceback and libmpv's own diagnostics on disk."""
    class FakeProc:
        pid = 51

    log_file = tmp_path / "portrait_satellite.log"
    with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        launch_satellite(
            python_exe="pythonw.exe",
            satellite_module="satellite",
            title="Portrait AI Player",
            role="Portrait",
            playlist_file="state/portrait_playlist.tsv",
            command_file="state/portrait_cmd.txt",
            paused_file="state/portrait_paused.txt",
            status_file="state/portrait_status.txt",
            log_file=log_file,
            x=0, y=0, width=1, height=1,
        )

    stream = popen.call_args.kwargs["stdout"]
    assert popen.call_args.kwargs["stderr"] is stream
    assert Path(stream.name) == log_file
    # The launcher keeps no handle of its own: a long-lived orchestrator would
    # otherwise leak one per launch and block the log ever being rolled aside.
    assert stream.closed
    assert "-m satellite" in log_file.read_text(encoding="utf-8")


def test_build_satellite_launch_command_forwards_the_hud_files():
    """The HUD is drawn inside the player now, so each satellite is told which
    panel file to render and where to post the clicks on it."""
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Portrait AI Player",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        hud_file="state/portrait_hud.json", dashboard_cmd_file="state/dashboard_cmd.txt",
        x=0, y=0, width=1, height=1,
    )

    assert cmd[cmd.index("--hud-file") + 1] == "state/portrait_hud.json"
    assert cmd[cmd.index("--dashboard-cmd-file") + 1] == "state/dashboard_cmd.txt"


def test_build_satellite_launch_command_omits_an_absent_hud():
    """No HUD file means no HUD: the flags are dropped rather than passed empty,
    so a satellite launched without one simply draws no map."""
    cmd = _build_satellite_launch_command(
        "python.exe", "satellite",
        title="Portrait AI Player",
        playlist_file="p", command_file="c", paused_file="pa", status_file="s",
        hud_file=None, dashboard_cmd_file=None,
        x=0, y=0, width=1, height=1,
    )

    assert "--hud-file" not in cmd
    assert "--dashboard-cmd-file" not in cmd


class TestEveryChildIsLaunchedUnderAFunTimeName:
    """Each app a session starts runs through a copy of the interpreter named
    for its role, so the task list says which rows are Fun Time's.

    Without this a stranded child is an anonymous ``pythonw.exe`` among the
    user's other Python apps, and the only way to end it is to guess.  Asserted
    at each launch site rather than on ``NAMER.named_exe`` alone: the
    module can be right while a launcher still passes the plain interpreter
    straight through, which is exactly how the audio companion came to be the
    one process nobody could name.
    """

    @staticmethod
    def _interpreter(tmp_path: Path) -> Path:
        # A real interpreter, because naming one writes a description into its
        # version resource and only a real executable can carry one — a stub
        # file falls back to being launched unnamed, which is the very thing
        # these assert against.
        exe = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sys.executable, exe)
        return exe

    @staticmethod
    def _launched_exe(popen) -> str:
        return Path(popen.call_args[0][0][0]).name

    def test_the_audio_companion(self, tmp_path: Path):
        class FakeProc:
            pid = 7

        with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            launch_ui_companions(
                python_exe=self._interpreter(tmp_path),
                dashboard_module="fun_time.dashboard_app",
                dashboard_enabled=False,
                windows_bridge_manifest_path=tmp_path / "manifest.ini",
                dashboard_x=0, dashboard_y=0, dashboard_width=1, dashboard_height=1,
                rfb_x=0, rfb_y=0, rfb_width=1, rfb_height=1,
                audio_module="fun_time.audio_companion_app",
                config_path=tmp_path / "config.json",
                audio_folder=tmp_path / "audio",
                result_file=tmp_path / "result.ini",
            )

        assert self._launched_exe(popen) == "FunTime-AudioCompanion.exe"

    def test_the_dashboard(self, tmp_path: Path):
        class FakeProc:
            pid = 8

        with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            launch_ui_companions(
                python_exe=self._interpreter(tmp_path),
                dashboard_module="fun_time.dashboard_app",
                dashboard_enabled=True,
                windows_bridge_manifest_path=tmp_path / "manifest.ini",
                dashboard_x=0, dashboard_y=0, dashboard_width=1, dashboard_height=1,
                rfb_x=0, rfb_y=0, rfb_width=1, rfb_height=1,
                audio_module="fun_time.audio_companion_app",
                config_path=tmp_path / "config.json",
                audio_folder=tmp_path / "audio",
                result_file=tmp_path / "result.ini",
            )

        assert Path(popen.call_args_list[0][0][0][0]).name == "FunTime-Dashboard.exe"

    def test_each_satellite_under_the_side_it_plays(self, tmp_path: Path):
        class FakeProc:
            pid = 9

        for role, title in (("Portrait", "Portrait AI Player"), ("Landscape", "Landscape AI Player")):
            with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
                "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
            ):
                launch_satellite(
                    python_exe=self._interpreter(tmp_path),
                    satellite_module="satellite",
                    title=title,
                    role=role,
                    playlist_file="playlist.tsv",
                    command_file="cmd.txt",
                    paused_file="paused.txt",
                    status_file="status.txt",
                    log_file=tmp_path / f"{role}.log",
                    x=0, y=0, width=1, height=1,
                )

            assert self._launched_exe(popen) == f"FunTime-{role}.exe"

    def test_nau_and_genau(self, tmp_path: Path):
        class FakeProc:
            pid = 10

        with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            launch_genau(
                python_exe=self._interpreter(tmp_path),
                genau_module="genau",
                config_path=tmp_path / "genau.json",
                clips_folder=tmp_path / "clips",
                genau_x=0, genau_y=0, genau_width=1, genau_height=1,
            )
        assert self._launched_exe(popen) == "FunTime-Genau.exe"

        with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            launch_nau(
                python_exe=self._interpreter(tmp_path),
                nau_module="nau",
                config_path=tmp_path / "genau.json",
                playlist_file="playlist.tsv",
                command_file="cmd.txt",
                paused_file="paused.txt",
                status_file="status.txt",
                console_file="console.json",
                drive_file="drive.txt",
                dashboard_cmd_file="dash.txt",
                log_file=tmp_path / "nau.log",
                nau_x=0, nau_y=0, nau_width=1, nau_height=1,
            )
        assert self._launched_exe(popen) == "FunTime-Nau.exe"

    def test_the_hosted_origenerator(self, tmp_path: Path):
        class FakeProc:
            pid = 11

        plan = WindowLayoutPlan(
            portrait=WindowRect(x=2560, y=0, width=1440, height=1870),
            landscape=WindowRect(x=853, y=0, width=1707, height=1440),
            dashboard=WindowRect(x=0, y=0, width=853, height=206),
            random_favs_browser=WindowRect(x=0, y=206, width=853, height=1234),
        )
        with patch("fun_time.windows_bridge_startup.subprocess.Popen", return_value=FakeProc()) as popen, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            launch_origenerator(
                python_exe=self._interpreter(tmp_path),
                origenerator_dir=tmp_path / "origenerator",
                layout_plan=plan,
                command_file="state/origenerator_cmd.txt",
                paused_file="state/origenerator_paused.txt",
                status_file="state/origenerator_status.txt",
                dashboard_cmd_file="state/dashboard_cmd.txt",
            )

        assert self._launched_exe(popen) == "FunTime-Origenerator.exe"

    def test_the_satellite_reap_can_still_find_a_player_under_its_new_name(self, tmp_path: Path):
        """The reap that clears stranded players bounds itself by image name.
        Renaming the players without widening it would leave every one of them
        beyond the reach of the sweep written to collect them."""
        with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            reap_orphaned_satellites("satellite", [tmp_path / "portrait_status.txt"])

        ps_command = run.call_args[0][0][-1]
        assert "FunTime-" in ps_command
        assert "pythonw?" in ps_command


def test_launch_origenerator_speaks_the_fun_time_contract(tmp_path: Path):
    """The argv is origenerator's --fun-time contract: the RFB rect as the main
    window's, both satellite region rects, the channel files, and the session's
    taskbar identity — run from the checkout so ``-m`` resolves that checkout's
    code, exactly like its own launcher does."""

    class FakeProc:
        pid = 77

    plan = WindowLayoutPlan(
        portrait=WindowRect(x=2560, y=0, width=1440, height=1870),
        landscape=WindowRect(x=853, y=0, width=1707, height=1440),
        dashboard=WindowRect(x=0, y=0, width=853, height=206),
        random_favs_browser=WindowRect(x=0, y=206, width=853, height=1234),
    )
    with patch("fun_time.windows_bridge_startup.subprocess.Popen",
               return_value=FakeProc()) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs",
        return_value={"creationflags": 1},
    ):
        pid = launch_origenerator(
            python_exe="C:/py/python.exe",
            origenerator_dir=tmp_path / "origenerator",
            layout_plan=plan,
            command_file="state/origenerator_cmd.txt",
            paused_file="state/origenerator_paused.txt",
            status_file="state/origenerator_status.txt",
            dashboard_cmd_file="state/dashboard_cmd.txt",
        )

    assert pid == 77
    command = popen.call_args.args[0]
    assert command[:3] == ["C:/py/python.exe", "-m", "origenerator"]
    assert "--fun-time" in command
    for flag, value in (
        ("--x", "0"), ("--y", "206"), ("--width", "853"), ("--height", "1234"),
        ("--portrait_x", "2560"), ("--portrait_height", "1870"),
        ("--landscape_x", "853"), ("--landscape_width", "1707"),
        ("--command-file", "state/origenerator_cmd.txt"),
        ("--paused-file", "state/origenerator_paused.txt"),
        ("--status-file", "state/origenerator_status.txt"),
        ("--dashboard-cmd-file", "state/dashboard_cmd.txt"),
        ("--taskbar-identity", APP_USER_MODEL_ID),
    ):
        assert flag in command, flag
        assert command[command.index(flag) + 1] == value, flag
    # cwd is what picks the checkout: -m resolves the package from it.
    assert popen.call_args.kwargs["cwd"] == str(tmp_path / "origenerator")
    # A primary checkout is the live install — no branch-session flag.
    assert "env" not in popen.call_args.kwargs


def test_hosting_a_worktree_runs_it_as_a_branch_session(tmp_path: Path):
    """A worktree checkout is unlanded code under judgment, not the live
    install: it seeds its database from the primary's and skips the library
    maintenance only the live app should run — origenerator's own preview
    launcher sets the same flag for the same reason."""

    class FakeProc:
        pid = 78

    plan = WindowLayoutPlan(
        portrait=WindowRect(x=0, y=0, width=10, height=20),
        landscape=WindowRect(x=0, y=0, width=20, height=10),
        dashboard=WindowRect(x=0, y=0, width=10, height=10),
        random_favs_browser=WindowRect(x=0, y=0, width=10, height=10),
    )
    worktree = tmp_path / "origenerator" / ".claude" / "worktrees" / "my-branch"
    with patch("fun_time.windows_bridge_startup.subprocess.Popen",
               return_value=FakeProc()) as popen, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={},
    ):
        launch_origenerator(
            python_exe="C:/py/python.exe",
            origenerator_dir=worktree,
            layout_plan=plan,
            command_file="c.txt", paused_file="p.txt",
            status_file="s.txt", dashboard_cmd_file="d.txt",
        )

    env = popen.call_args.kwargs["env"]
    assert env["ORIGENERATOR_BRANCH_SESSION"] == "1"
