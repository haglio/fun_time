"""Unit tests for the integration test-support harness (teardown safety).

These guard the deterministic child-process cleanup that
``FunTimeIntegrationSession.stop()`` must perform.  ``stop()`` hard-terminates
the orchestrator, so the orchestrator's own graceful ``_shutdown_children()``
never runs and its children (the two satellites, plus Main Funestra/Genau/dashboard/
audio) are orphaned.  The teardown must therefore kill them itself, by the
exact processes recorded in ``bridge_pids.ini`` — PID *and* creation time, so a
PID Windows has since recycled is recognized rather than shot.
"""
from __future__ import annotations

import ast
import itertools
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from app_support import haglio_settings
from funestra.flick_folder import SUPPORTED_VIDEO_EXTS
from funestra.playlist import read_playlist

from fun_time import funestra_status, windows_bridge_orchestrator
from fun_time.config import load_config
from fun_time.funestra_status import MainFunestraStatus
from fun_time.loopback_server import LOOPBACK_PORT
from fun_time.main_list import MainListSettings
from fun_time.manifest import LaunchManifest, write_windows_bridge_manifest
from fun_time.monitors import enumerate_monitors
from fun_time.windows_bridge_dispatch_loop import build_bridge_config_from_manifest
from fun_time.windows_bridge_orchestrator import ChildProcess
from tests.integration import integration_support, run_clock
from tests.integration.integration_support import (
    COMMAND_BUDGET_S,
    INTEGRATION_CONFIG_NAME,
    SESSION_MONITORS,
    FunTimeIntegrationSession,
    await_window,
    build_integration_config,
    close_the_runs_osr2s,
    ended_within,
    genau_where_this_machine_keeps_it,
    isolate_shared_resources,
    joined_within,
    let_seconds_pass,
    library_clips,
    point_the_main_funestra_at,
    published_status,
    readable_at_speed,
    run_within,
    sample_library_clips,
    stall_per_transition,
    tcode_heard,
    wait_for,
    window_is_quiet,
)
from tests.integration.run_clock import keep_time_by
from tests.made_up_clocks import SleepDrivenClock, StarvedRun
from tests.made_up_machine import running

OSR2_SLOT_PORT = 50558
GENAU_INBOUND_PORT = 50555
AUDIO_COMPANION_PORT = 50556

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "fun_time_config.example.json"


def _the_users_config() -> tuple[dict, dict]:
    """The two configs as the user's own session has them, naming what the machine shares.

    Fun Time's side is read from the committed example his real config is written
    from, so a section that moves into it reaches every check here the day it lands.
    """
    config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    # A session may be pinned at the broker's own directory rather than its own
    # state dir — a branch session is — and a run copies the config whole.
    config["paths"]["broker_state_dir"] = "C:/Users/Example/workspace/fun_time/state"
    config["voice_control"]["enabled"] = True
    config["loopback_port"] = LOOPBACK_PORT
    # Vestigial: fun_time stopped parsing this when Genau moved to its own
    # repo, but the section is still sitting in every config file written
    # before then — including the one a run copies.
    config["genau"] = {"udp_host": "127.0.0.1", "udp_port": GENAU_INBOUND_PORT,
                       "notify_port": AUDIO_COMPANION_PORT, "status_hide_ms": 1200}
    genau_config = {
        "genau": {
            "udp_port": GENAU_INBOUND_PORT,
            "notify_host": "127.0.0.1",
            "notify_port": AUDIO_COMPANION_PORT,
        },
    }
    return config, genau_config


@pytest.fixture
def isolated_ports():
    """The rewritten pair, with the run's own slot for the OSR2 closed afterwards."""
    config, genau_config = _the_users_config()
    isolate_shared_resources(config, genau_config)
    try:
        yield config, genau_config
    finally:
        close_the_runs_osr2s()


LIVE_ORCHESTRATOR = (
    61, "python.exe", r"python.exe -m fun_time.orchestrator --config C:\Fun Time\fun_time_config.json", 0.0)
A_RUNS_ORCHESTRATOR = (
    62, "python.exe", rf"python.exe -m fun_time.orchestrator --config C:\Temp\run\{INTEGRATION_CONFIG_NAME}", 0.0)


@pytest.fixture
def session(cfg_path):
    return FunTimeIntegrationSession(cfg_path)


def _write_bridge_pids(session: FunTimeIntegrationSession, children: dict[str, ChildProcess]) -> None:
    pids_file = session.config.paths.state_dir / "bridge_pids.ini"
    pids_file.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            "[pids]",
            *(f"{key}={child.pid}" for key, child in children.items()),
            "[created_at]",
            *(f"{key}={child.created_at}" for key, child in children.items()),
        ]
    )
    pids_file.write_text(body + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _hermetic_stop(session, monkeypatch):
    """Neutralize the side-effecting halves of stop() so tests stay hermetic.

    The desktop-scoped leftover sweep is exercised elsewhere; here we isolate
    the deterministic identity-checked kill path.
    """
    monkeypatch.setattr(session, "_reap_leftover_runtime_processes", lambda: None)


def test_stop_taskkills_every_recorded_child(session):
    _write_bridge_pids(
        session,
        {
            "main_funestra_pid": ChildProcess(201, 2010),
            "portrait_pid": ChildProcess(202, 2020),   # satellite
            "landscape_pid": ChildProcess(203, 2030),  # satellite
            "dashboard_pid": ChildProcess(0, 0),       # disabled in integration — absent
            "origenerator_pid": ChildProcess(205, 2050),
            "audio_pid": ChildProcess(206, 2060),
        },
    )
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "get_process_creation_time", side_effect=lambda pid: pid * 10), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()

    # Every recorded child with a real PID is killed by that exact PID; the
    # zero placeholder (disabled dashboard) is skipped.
    assert sorted(killed) == [201, 202, 203, 205, 206]


def test_stop_waits_for_every_child_it_killed_to_be_gone(session):
    """A test that opens a second session over the same state dir hands it a
    Funestra still publishing: the one dying holds its own status file's temp name,
    and the new Funestra's first write of it dies on the sharing violation, which
    takes the whole startup down (seen on run 4 of a flake gate, 2026-10-06)."""
    _write_bridge_pids(session, {"main_funestra_pid": ChildProcess(201, 2010)})
    alive = [True, True, False]
    asked: list[int] = []

    def is_alive(pid: int) -> bool:
        asked.append(pid)
        return alive.pop(0)

    with patch.object(windows_bridge_orchestrator, "get_process_creation_time",
                      side_effect=lambda pid: pid * 10), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", lambda _pid: None), \
         patch.object(integration_support, "is_process_alive", side_effect=is_alive), \
         patch.object(integration_support.time, "sleep", lambda _s: None):
        session.stop()

    assert asked == [201, 201, 201], "stop returned with a killed child still alive"


def test_stop_gives_up_on_a_child_that_never_goes(session):
    """Bounded: a pid Windows will not reap must not hold the suite for good."""
    _write_bridge_pids(session, {"main_funestra_pid": ChildProcess(201, 2010)})

    with patch.object(windows_bridge_orchestrator, "get_process_creation_time",
                      side_effect=lambda pid: pid * 10), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", lambda _pid: None), \
         patch.object(integration_support, "is_process_alive", return_value=True), \
         patch.object(integration_support.time, "sleep", lambda _s: None):
        session.stop()  # returns rather than looping for good


def test_stop_does_not_kill_a_recorded_pid_windows_recycled(session):
    """Killing a recycled PID is how a run murders another run's pytest: the
    dead child's PID now belongs to somebody else."""
    _write_bridge_pids(session, {"audio_pid": ChildProcess(205, 2050)})
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "get_process_creation_time", return_value=9999), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()

    assert killed == []


class RunningOrchestrator:
    def __init__(self, steps):
        self.steps = steps

    def poll(self):
        return 1 if "terminate" in self.steps else None

    def terminate(self):
        self.steps.append("terminate")

    def wait(self, timeout=None):
        return 1

    def kill(self):
        self.steps.append("kill")


def test_a_session_measured_for_coverage_quits_the_way_he_quits_before_anything_is_ended(session, monkeypatch):
    steps: list[str] = []
    monkeypatch.setenv("COVERAGE_PROCESS_START", "coveragerc")
    session._proc = RunningOrchestrator(steps)
    monkeypatch.setattr(session, "quit_gracefully", lambda timeout=None: steps.append("quit"))

    session.stop()

    assert steps[:1] == ["quit"]


def test_a_session_nobody_measures_is_ended_at_once(session, monkeypatch):
    steps: list[str] = []
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)
    session._proc = RunningOrchestrator(steps)
    monkeypatch.setattr(session, "quit_gracefully", lambda timeout=None: steps.append("quit"))

    session.stop()

    assert steps == ["terminate"]


def test_every_session_lays_its_room_out_on_the_same_two_monitors_whatever_the_machine_lists(
        session, monkeypatch):
    launched = {}
    monkeypatch.setattr(integration_support.subprocess, "Popen",
                        lambda argv, **kwargs: launched.update(kwargs))
    session._launch(project_dir=None, env_overrides=None)
    session._stderr_fh.close()
    monkeypatch.setenv("FUN_TIME_FAKE_MONITORS", launched["env"].get("FUN_TIME_FAKE_MONITORS", ""))

    assert enumerate_monitors() == list(SESSION_MONITORS)


def test_stop_survives_missing_bridge_pids(session):
    """A session that failed before writing bridge_pids.ini must still tear
    down without raising — and without trying to kill anything by PID."""
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()  # no bridge_pids.ini on disk

    assert killed == []


def test_a_timed_out_wait_says_when_each_funestra_last_published(session):
    main_funestra_status = session.config.main_funestra_status_file
    main_funestra_status.parent.mkdir(parents=True, exist_ok=True)
    main_funestra_status.write_text("paused=0\n", encoding="utf-8")
    two_minutes_ago = time.time() - 120
    os.utime(main_funestra_status, (two_minutes_ago, two_minutes_ago))

    with pytest.raises(AssertionError) as timed_out:
        session.wait_until(lambda: False, timeout=0, description="the Main Funestra to hold still")

    assert "Main Funestra 120s ago; portrait never; landscape never" in str(timed_out.value)


@pytest.fixture
def run_time():
    yield keep_time_by
    keep_time_by(run_clock.WALL)


@pytest.fixture
def clock(monkeypatch, run_time):
    fake = SleepDrivenClock()
    monkeypatch.setattr(integration_support, "time", fake)
    run_time(fake)
    return fake


@pytest.mark.parametrize("wait", [
    lambda session: session.wait_until(lambda: False),
    lambda session: session.wait_for_log("never logged"),
    lambda session: session.wait_for_new_log("never logged"),
    lambda session: session.wait_for_any_log(["never logged"]),
    lambda session: wait_for(lambda: None, desc="something that never comes"),
], ids=["wait_until", "wait_for_log", "wait_for_new_log", "wait_for_any_log", "wait_for"])
def test_a_wait_named_without_a_budget_is_given_the_familys_command_budget(session, clock, wait):
    with pytest.raises(AssertionError):
        wait(session)

    assert COMMAND_BUDGET_S <= clock.now < COMMAND_BUDGET_S + 1


def test_a_wait_hands_back_what_it_found(clock):
    answers = iter([None, "", "the clip"])

    assert wait_for(lambda: next(answers), desc="a clip") == "the clip"


def test_a_wait_that_runs_out_says_what_it_waited_for_and_what_it_last_saw(clock):
    with pytest.raises(AssertionError, match=r"the satellite to lock \(last=0\)"):
        wait_for(lambda: 0, desc="the satellite to lock")


def test_a_wait_that_runs_out_ends_with_what_it_was_handed_to_explain_it(clock):
    with pytest.raises(AssertionError, match=r"\(last=None\)\nthe clip never opened"):
        wait_for(lambda: None, desc="the satellite to start playing",
                 explain=lambda: "the clip never opened")


def test_a_wait_is_counted_in_the_seconds_the_run_could_run(monkeypatch, run_time):
    """Ten thousand polls are half an hour on the wall, and none of it the run's
    while normal-priority work holds every processor."""
    monkeypatch.setattr(integration_support, "time", SleepDrivenClock())
    run_time(StarvedRun())
    polls = iter(range(10_000))

    assert wait_for(lambda: next(polls) == 9_999, desc="a Funestra starved of the processor")


def test_a_window_is_awaited_by_its_whole_title_in_the_seconds_the_run_could_run(monkeypatch, run_time):
    monkeypatch.setattr(integration_support, "time", SleepDrivenClock())
    run_time(StarvedRun())
    looks = iter(range(10_000))
    monkeypatch.setattr(integration_support, "find_window_by_title",
                        lambda _title, *, exact: 7 if next(looks) == 9_999 and exact else 0)

    assert await_window("Portrait AI Funestra", 10.0, exact=True) == 7


def test_a_process_is_waited_on_in_the_seconds_the_run_could_run(monkeypatch, run_time):
    monkeypatch.setattr(integration_support, "time", SleepDrivenClock())
    run_time(StarvedRun())
    polls = iter(range(10_000))

    assert ended_within(SimpleNamespace(poll=lambda: 0 if next(polls) == 9_999 else None), 5.0)


def test_a_thread_is_waited_on_in_the_seconds_the_run_could_run(monkeypatch, run_time):
    monkeypatch.setattr(integration_support, "time", SleepDrivenClock())
    run_time(StarvedRun())
    looks = iter(range(10_000))

    assert joined_within(SimpleNamespace(is_alive=lambda: next(looks) < 9_999), 5.0)


def test_a_command_is_run_to_its_end_in_the_seconds_the_run_could_run(monkeypatch, run_time):
    run_time(StarvedRun())
    asked = iter(range(10_000))

    def communicate(timeout):
        if next(asked) < 9_999:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return "made", ""

    monkeypatch.setattr(subprocess, "Popen", lambda _argv, **_popen: SimpleNamespace(
        communicate=communicate, returncode=0))

    assert run_within(["ffmpeg"], 60.0, check=True).stdout == "made"


def test_a_command_still_running_when_its_seconds_are_spent_is_ended_and_named_with_them(
        monkeypatch, clock):
    ended: list[str] = []

    def communicate(timeout=None):
        if ended:
            return "", ""
        clock.sleep(timeout)
        raise subprocess.TimeoutExpired("ffmpeg", timeout)

    monkeypatch.setattr(subprocess, "Popen", lambda _argv, **_popen: SimpleNamespace(
        communicate=communicate, returncode=None, kill=lambda: ended.append("killed")))

    with pytest.raises(subprocess.TimeoutExpired, match="after 60.0 seconds"):
        run_within(["ffmpeg"], 60.0)
    assert ended == ["killed"]


class _StarvedFor:
    def __init__(self, looks: int) -> None:
        self._looks = looks

    def seconds(self) -> float:
        self._looks -= 1
        return 0.0 if self._looks > 0 else 1e9


def test_a_move_sent_to_the_osr2_while_the_run_is_starved_is_still_heard(monkeypatch, run_time):
    monkeypatch.setattr(integration_support, "time", SleepDrivenClock())
    run_time(_StarvedFor(10_000))
    asked = itertools.count()

    def take():
        return [(0.0, "portrait", "L0999")] if next(asked) == 5_000 else []

    monkeypatch.setattr(integration_support, "the_runs_osr2",
                        lambda _port: SimpleNamespace(take=take))

    assert [sender for _at, sender, _height in tcode_heard(50_000, seconds=3)] == ["portrait"]


def test_the_seconds_a_check_lets_pass_are_the_seconds_the_run_could_run(monkeypatch, run_time):
    wall = SleepDrivenClock()
    monkeypatch.setattr(integration_support, "time", wall)
    run_time(_StarvedFor(1_000))

    let_seconds_pass(1.0)

    assert wall.now > 10.0


def test_the_reap_never_waits_on_a_hotkey_script_it_did_not_end(session, monkeypatch):
    """His own session's hotkey script runs whenever he has Fun Time open, so a
    wait for every AutoHotkey on the machine spent its whole budget at both ends
    of every test; the ones the reap ends, kill_process_tree waits out itself."""
    monkeypatch.setattr(integration_support, "_kill_leftover_app_processes", lambda: None)
    monkeypatch.setattr(session, "_wait_for_orchestrators_to_exit", lambda: None)
    monkeypatch.setattr(integration_support.time, "sleep",
                        lambda _seconds: pytest.fail("the reap waited"))

    with patch("subprocess.Popen", side_effect=AssertionError("started a program")):
        FunTimeIntegrationSession._reap_leftover_runtime_processes(session)


def test_the_orchestrator_wait_only_ever_waits_on_integration_orchestrators(session):
    """Between a session's teardown and the next one's start, the harness waits
    for the *previous run's* orchestrator to finish its shutdown storm — that
    orchestrator taskkills the PIDs it recorded, and Windows recycles PIDs fast
    enough for it to shoot a new session's freshly-spawned ones.

    Matching every ``fun_time.orchestrator`` on the machine makes that wait
    include the user's live session, which will not exit — so the harness burns
    its whole 15s timeout on both ends of every session, and its own teardown
    becomes hostage to a session it has nothing to do with.  Only orchestrators
    started from an integration config can be the one we are waiting on.
    """
    with running("tests.integration.integration_support", [LIVE_ORCHESTRATOR]), \
         patch.object(integration_support.time, "sleep",
                      side_effect=AssertionError("it waited on the live session")):
        session._wait_for_orchestrators_to_exit()


def test_the_orchestrator_wait_holds_until_the_last_integration_orchestrator_is_gone(session):
    machine = [LIVE_ORCHESTRATOR, A_RUNS_ORCHESTRATOR]

    with running("tests.integration.integration_support", machine), \
         patch.object(integration_support.time, "sleep",
                      side_effect=lambda _seconds: machine.remove(A_RUNS_ORCHESTRATOR)) as slept:
        session._wait_for_orchestrators_to_exit()

    assert slept.call_count == 1


def test_the_integration_config_never_shares_the_live_sessions_audio_port(isolated_ports):
    """The audio companion binds a fixed UDP port, and ``build_integration_config``
    rewrote only *paths* — so a run and a live session raced for one socket.

    Whichever bound second died with WSAEADDRINUSE.  Started in the order the
    user would notice, that is theirs: a run holding the port means opening Fun
    Time loses its companion audio, with nothing on screen to explain it.  And
    while both were up, Genau's notifications went to whichever companion won,
    which need not be its own session's.
    """
    config, genau_config = isolated_ports

    assert config["audio_companion"]["port"] != AUDIO_COMPANION_PORT
    # Sender and receiver have to move together: Genau notifies the port the
    # companion is listening on, so rewriting one alone just breaks the run.
    assert genau_config["genau"]["notify_port"] == config["audio_companion"]["port"]


def test_the_integration_config_never_reaches_the_machines_osr2(isolated_ports):
    """T-Code is the one output that reaches hardware.

    Every Funestra and Genau reach the OSR2 through the broker's slot, and the
    broker holds the OSR2's serial port — one process, one device, for the whole
    machine.  A run that kept the production slot would therefore drive the
    user's OSR2 while they are using it, and no amount of desktop or state-dir
    isolation touches that: a socket is not per-desktop.
    """
    config, _genau_config = isolated_ports

    assert config["osr2_slot_port"] != OSR2_SLOT_PORT
    assert integration_support.the_runs_osr2(config["osr2_slot_port"]).port == config["osr2_slot_port"]


def test_a_runs_main_funestra_browses_the_runs_copy_of_the_library_not_the_machines():
    config, _genau_config = _the_users_config()
    run_videos = Path("C:\\Temp\\fun_time_integration_run\\integration_runtime\\videos\\videos\\primary")

    point_the_main_funestra_at(config, run_videos)

    assert config["main_player"]["videos_dir"] == str(run_videos)


def _a_machines_config_over_a_made_up_library(root: Path) -> Path:
    library = root / "videos"
    films = library / "videos" / "films"
    scripts = library / "scripts" / "scripts" / "films"
    sides = {side: library / "videos" / "made" / side for side in ("portrait", "landscape")}
    for folder in (films / "full", scripts / "full", *sides.values()):
        folder.mkdir(parents=True)
    for name in ("scene one", "scene two"):
        (films / "full" / f"{name}.mp4").write_bytes(b"")
        (scripts / "full" / f"{name}.funscript").write_text("{}", encoding="utf-8")
    for folder in sides.values():
        for name in ("alpha", "beta"):
            (folder / f"{name}.mp4").write_bytes(b"")
    config, genau_config = _the_users_config()
    genau_config_path = root / "genau_config.json"
    genau_config_path.write_text(json.dumps(genau_config), encoding="utf-8")
    config["paths"].update(
        main_player_library_dirs=[str(films)],
        portrait_dirs=[str(sides["portrait"])], landscape_dirs=[str(sides["landscape"])],
        genau_config_path=str(genau_config_path), state_dir=str(root / "state"))
    config["main_player"]["videos_dir"] = str(films)
    config["regen"]["metadata_root"] = str(library / "metadata")
    config_path = root / "fun_time_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def test_a_runs_room_plays_each_main_video_with_the_funscript_the_run_copied_for_it(
        tmp_path, monkeypatch):
    machine = _a_machines_config_over_a_made_up_library(tmp_path / "machine")
    monkeypatch.setattr(integration_support, "real_config_path", lambda: machine)
    run = tmp_path / "run"
    try:
        config = load_config(build_integration_config(run))
        manifest = LaunchManifest.read(write_windows_bridge_manifest(
            config, config.paths.state_dir / "windows_bridge_launch.ini"))
        main_list = build_bridge_config_from_manifest(manifest).main_list
        main_list.write(MainListSettings())
    finally:
        close_the_runs_osr2s()

    played = read_playlist(main_list.file)
    assert played
    assert all(item.funscript is not None and run in item.funscript.parents for item in played), [
        item.funscript for item in played]


def test_the_runs_slot_listens_so_the_runs_senders_have_somewhere_to_connect(isolated_ports):
    config, _genau_config = isolated_ports

    with socket.create_connection(("127.0.0.1", config["osr2_slot_port"]), timeout=5.0):
        pass


def test_a_run_serves_its_loopback_surface_somewhere_of_its_own(isolated_ports):
    """8770 is machine-wide, and the loser of a race for it loses the surface
    entirely — a warning in a log, then no Tampermonkey auto-update and RFB tab
    pages that never hear about OmniPause.  A run held it for minutes at a time
    and the guard is what kept the user's session from being that loser.
    """
    config, _genau_config = isolated_ports

    assert config["loopback_port"] != LOOPBACK_PORT


def test_a_run_never_starts_or_adopts_the_machines_broker(isolated_ports):
    """The broker is a machine singleton holding the OSR2's serial port.

    A session start launches its tray, and the dashboard's broker actions can
    kill every broker on the machine — a sweep matched by command line, with
    nothing for a working directory to scope.  Neither belongs to a test run:
    the broker is not the run's to start, and is emphatically not the run's to
    kill out from under the user.

    Emptying the launcher is what makes all of it inert, without a second mode
    in the production code: ``start_broker`` and ``launch_broker_tray`` both
    do nothing without one, and the kill path is only ever reached through a
    *fresh* heartbeat — which a run's own state dir, where no broker writes,
    can never show.
    """
    config, _genau_config = isolated_ports

    assert config["paths"]["broker_tray_launcher"] == ""


def test_a_run_never_inherits_a_pin_at_the_machines_broker_directory(isolated_ports):
    """The broker's files are the one part of ``state/`` a session may be pointed
    away from its own directory for — a branch session is, because the machine's
    one broker writes where its own config says and nowhere else.

    A run copies the config whole, so it would inherit that pin: park and retract
    written into the live broker's command file while the user is using it, and
    the live heartbeat read back as the run's own — which is precisely the fresh
    heartbeat the broker kill path above is only ever reached through.  Dropped,
    the whole channel falls back inside the run's own state dir.
    """
    config, _genau_config = isolated_ports

    assert "broker_state_dir" not in config["paths"]


def test_a_run_never_opens_the_microphone(isolated_ports):
    """Voice control resolves its mic by name and opens the machine's one Brio.

    Windows shares an input device between listeners rather than refusing the
    second, so a run would not fail — it would quietly listen in on the user and
    act on what they said to their own session.  There is one microphone and no
    per-desktop version of it, so the only isolation is not opening it.
    """
    config, _genau_config = isolated_ports

    assert config["voice_control"]["enabled"] is False


def test_the_integration_config_never_shares_genaus_inbound_socket(isolated_ports):
    """Genau *binds* its UDP port to hear from the broker, and binds it with
    SO_REUSEADDR — which on Windows lets a second Genau bind the same port
    rather than refusing it.  Two Genaus then split one datagram stream between
    them at random, so a run's Genau would swallow packets meant for the user's.
    """
    _config, genau_config = isolated_ports

    assert genau_config["genau"]["udp_port"] != GENAU_INBOUND_PORT


def _every_value(node) -> list:
    """Every leaf value in a nested config, wherever it is nested."""
    if isinstance(node, dict):
        return [leaf for child in node.values() for leaf in _every_value(child)]
    if isinstance(node, list):
        return [leaf for child in node for leaf in _every_value(child)]
    return [node]


def test_no_endpoint_of_the_machines_survives_anywhere_in_a_runs_config(isolated_ports):
    """The completeness check the whole arrangement rests on.

    Each test above names one shared endpoint and says why it matters; between
    them they are the entire list, and this sweeps both configs to prove it —
    including any field that mentions one of these ports in passing, or a new
    field that quietly copies one in.  A run that holds none of them cannot
    reach the user's session at all, which is what lets the two share a machine
    instead of taking turns on it.
    """
    config, genau_config = isolated_ports
    machines_own = {AUDIO_COMPANION_PORT, GENAU_INBOUND_PORT, OSR2_SLOT_PORT, LOOPBACK_PORT}

    survivors = [
        value
        for value in _every_value(config) + _every_value(genau_config)
        if value in machines_own or str(value) in {str(port) for port in machines_own}
    ]

    assert survivors == []


def test_a_runs_sessions_start_their_programs_from_the_installation_the_run_runs_from():
    config = {"paths": {"python_exe": "C:/Users/Someone/workspace/fun_time/.venv/Scripts/pythonw.exe"}}

    integration_support.run_from_this_installation(config)

    assert config["paths"]["python_exe"] == str(Path(sys.executable).with_name("pythonw.exe"))


def test_a_run_never_hosts_the_machines_origenerator(isolated_ports):
    """The hosted Origenerator is a second app with machine ends of its own:
    the one ComfyUI server on its fixed port (which it would START if absent),
    the GPU that server generates on, and the app's one database.  None has a
    per-desktop version, so a run hosts none — the key gone means the session
    has no origenerator mode at all."""
    config, _genau_config = isolated_ports

    assert "origenerator_dir" not in config["paths"]
    assert "origenerator_python_exe" not in config["paths"]


def test_a_run_launches_this_checkouts_siblings(tmp_path, monkeypatch):
    """A run launches this checkout's code, so it has to launch this
    checkout's SIBLINGS too.

    A branch that leans on an unlanded ``funestra`` change — the satellites'
    HUD moved there, say — otherwise starts Funestras that import a name the
    primary's install does not have.  Every one of them dies at import, with no
    window and no status file, and the suite reads as a wall of timeouts rather
    than as a path problem: eight errors and six failures, none of them naming
    the import that actually failed.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    branch = tmp_path / "funestra_branch"
    branch.mkdir(exist_ok=True)
    (state_dir / "sibling_checkouts.txt").write_text(
        f"# a branch of a sibling\n{branch}\n", encoding="utf-8")
    monkeypatch.setattr(integration_support, "PROJECT_DIR", tmp_path)
    config: dict = {}

    integration_support.apply_this_checkouts_siblings(config)

    assert config["paths"]["sibling_checkouts"] == [str(branch)]
    assert integration_support.this_checkouts_siblings() == str(branch)


def test_a_checkout_with_no_override_names_no_siblings(tmp_path, monkeypatch):
    """Which is every ordinary checkout: the run launches exactly what a
    session on the machine's own config launches."""
    (tmp_path / "state").mkdir(exist_ok=True)
    monkeypatch.setattr(integration_support, "PROJECT_DIR", tmp_path)
    config: dict = {}

    integration_support.apply_this_checkouts_siblings(config)

    assert config == {}
    assert integration_support.this_checkouts_siblings() == ""


def test_a_run_finds_genau_where_this_machine_keeps_it_and_reads_the_rest_of_the_shared_settings_as_the_example(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".haglio").mkdir(parents=True)
    (home / ".haglio" / "settings.json").write_text(json.dumps({
        **haglio_settings.EXAMPLE, "genau_dir": "M:/machine/genau",
        "genau_audio_dir": "M:/machine/audio", "genau_flick_words": {"one": "pip", "many": "pips"},
    }), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(integration_support, "build_integration_temp_root", lambda: tmp_path / "run")
    (tmp_path / "run").mkdir()
    before_the_run = haglio_settings.value("genau_dir")

    with genau_where_this_machine_keeps_it():
        in_the_run = (haglio_settings.value("genau_dir"), haglio_settings.value("genau_audio_dir"),
                      haglio_settings.value("genau_flick_words"))

    assert in_the_run == (str(Path("M:/machine/genau")), str(Path("M:/machine/audio")),
                          haglio_settings.EXAMPLE["genau_flick_words"])
    assert haglio_settings.value("genau_dir") == before_the_run


def test_a_clip_reads_at_speed_only_when_its_first_bytes_arrive_in_time():
    """The VR masters sit on the pCloud drive, which fetches a file from the
    internet the first time anything here opens it; a Funestra fed one stalls
    on the fetch, not on the code under test."""
    def instant(path, size):
        return None

    def cold(path, size):
        time.sleep(0.5)

    def gone(path, size):
        raise OSError("no such file")

    assert readable_at_speed("warm.mp4", budget_s=0.2, read=instant)
    assert not readable_at_speed("cold.mp4", budget_s=0.05, read=cold)
    assert not readable_at_speed("gone.mp4", budget_s=0.2, read=gone)


def test_a_sample_skips_the_clips_the_drive_cannot_serve_at_speed(monkeypatch, capsys):
    monkeypatch.setenv("FUN_TIME_INTEGRATION_SEED", "7")

    chosen = sample_library_clips(
        ["a.mp4", "b.mp4", "c.mp4", "d.mp4"], 2, desc="clips",
        readable=lambda clip: clip != "b.mp4",
    )

    assert len(chosen) == 2 and "b.mp4" not in chosen
    assert "skipped one not readable at speed" in capsys.readouterr().out
    assert "b.mp4" not in capsys.readouterr().out


def test_a_sample_fails_saying_so_when_too_few_clips_read_at_speed():
    with pytest.raises(AssertionError, match="read at speed"):
        sample_library_clips(["a.mp4", "b.mp4"], 2, desc="clips", readable=lambda clip: False)


def test_a_sample_gives_up_probing_once_its_budget_is_spent(monkeypatch):
    """Every skip costs a real two-second wait, so a library the drive has gone
    cold on can outlast the whole test.  It did: pytest's timeout fired, its
    thread could not interrupt the blocked read, and the run sat there holding
    the machine-wide lock until somebody killed it by hand.  A budget makes that
    a named failure instead."""
    monkeypatch.setenv("FUN_TIME_INTEGRATION_SEED", "7")
    probed: list[str] = []
    clock = iter([0.0, 0.0, 1.0, 99.0, 99.0, 99.0])

    def probe(clip):
        probed.append(clip)
        return False

    with pytest.raises(AssertionError, match="the library is cold"):
        sample_library_clips([f"{n}.mp4" for n in range(50)], 2, desc="clips",
                             readable=probe, budget_s=10.0, now=lambda: next(clock))

    assert len(probed) == 2


def test_the_listing_keeps_only_what_the_funestras_play(tmp_path):
    for suffix in (*SUPPORTED_VIDEO_EXTS, ".wmv", ".funscript", ".txt"):
        (tmp_path / f"clip{suffix}").write_bytes(b"")

    assert sorted(p.suffix for p in library_clips([tmp_path])) == sorted(SUPPORTED_VIDEO_EXTS)


def _a_library_whose_videos_never_answer(library: Path, monkeypatch) -> None:
    asked_elsewhere = Path.is_file

    def is_file(path, *args, **kwargs):
        if library in path.parents:
            raise AssertionError(f"the draw asked {path.name} about itself, and a video that never answers holds the run")
        return asked_elsewhere(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file)


def test_the_main_funestras_draw_never_waits_on_a_video_it_lists(tmp_path, monkeypatch):
    library = tmp_path / "drawn" / "videos" / "videos"
    scripts = tmp_path / "drawn" / "videos" / "scripts" / "scripts"
    library.mkdir(parents=True)
    scripts.mkdir(parents=True)
    for name in ("one", "two"):
        (library / f"{name}.mp4").write_bytes(b"")
        (scripts / f"{name}.funscript").write_text("{}", encoding="utf-8")
    _a_library_whose_videos_never_answer(library, monkeypatch)
    room = tmp_path / "room" / "videos" / "videos"
    room.mkdir(parents=True)

    linked = integration_support._link_primary_samples(
        SimpleNamespace(paths=SimpleNamespace(main_funestra_library_dirs=[library]),
                        regen=SimpleNamespace(metadata_root=tmp_path / "drawn" / "videos" / "metadata")),
        room, metadata_root=tmp_path / "room" / "videos" / "metadata", count=2)

    assert sorted(path.name for path in linked) == ["one.mp4", "two.mp4"]


def test_the_main_funestras_draw_takes_only_the_videos_f_mode_plays(tmp_path, monkeypatch):
    library = tmp_path / "drawn" / "videos" / "videos"
    library.mkdir(parents=True)
    for name in ("played", "passed over"):
        (library / f"{name}.mp4").write_bytes(b"")
    machines_metadata_root = tmp_path / "drawn" / "videos" / "metadata"
    judged_against = set()

    def plays_in_f_mode(video, metadata_root):
        judged_against.add(metadata_root)
        return Path(video).stem == "played"

    monkeypatch.setattr(integration_support, "plays_in_f_mode", plays_in_f_mode)
    room = tmp_path / "room" / "videos" / "videos"
    room.mkdir(parents=True)

    linked = integration_support._link_primary_samples(
        SimpleNamespace(paths=SimpleNamespace(main_funestra_library_dirs=[library]),
                        regen=SimpleNamespace(metadata_root=machines_metadata_root)),
        room, metadata_root=tmp_path / "room" / "videos" / "metadata", count=2)

    assert [path.name for path in linked] == ["played.mp4"]
    assert judged_against == {machines_metadata_root}


def test_a_sides_draw_never_waits_on_a_video_it_lists(tmp_path, monkeypatch):
    library = tmp_path / "drawn"
    library.mkdir()
    for name in ("one", "two"):
        (library / f"{name}.mp4").write_bytes(b"")
    _a_library_whose_videos_never_answer(library, monkeypatch)
    room = tmp_path / "room"
    room.mkdir()

    linked = integration_support._link_sample_files((library,), room, count=2)

    assert sorted(path.name for path in linked) == ["one.mp4", "two.mp4"]


def test_the_vr_test_draws_nothing_from_the_vr_library():
    """The VR masters sit on the cloud drive, where opening a cold file blocks
    inside the drive's own driver: no timeout ends a thread stuck there, and
    Windows cannot finish closing a process that has one.  Every run that drew
    from them left an unkillable python process -- fifteen on 2026-09-12 -- so the
    VR test draws from the desktop library alone."""
    vr_test = Path(integration_support.__file__).with_name("test_vr_funestras_integration.py")

    assert "vr.library_dirs" not in vr_test.read_text(encoding="utf-8")


def test_a_window_whose_median_hides_a_stall_is_not_quiet():
    """The settle gate the VR pipeline test runs on.

    Its old form asked only for a median under budget, and a machine still
    tearing a session down answers that: measured on this machine, a probe
    window came in at a 9.5ms median with a 428ms frame inside it, was taken
    for settled, and the sample that followed blew the guard — reported as
    the pipeline pacing the loop when the pipeline was never asked."""
    quiet = [1.0] * 120
    hiding_a_stall = [1.0] * 80 + [40.0] * 39 + [428.0]

    assert window_is_quiet(quiet, budget_ms=11.1)
    assert not window_is_quiet(hiding_a_stall, budget_ms=11.1)
    assert sorted(hiding_a_stall)[len(hiding_a_stall) // 2] < 11.1, (
        "the window this rejects is one a median gate accepts"
    )


def test_a_quiet_window_tolerates_the_odd_frame():
    """One frame in a hundred over budget is a machine, not a regression: the
    gate is the 90th percentile, so a handful of stray frames never fails it
    while a window that is over budget most of the time always does."""
    assert window_is_quiet([1.0] * 115 + [50.0] * 5, budget_ms=11.1)
    assert not window_is_quiet([1.0] * 50 + [50.0] * 70, budget_ms=11.1)


def test_the_stall_a_transition_costs_is_the_typical_pass_not_the_worst():
    """What the clip-transition guard judges.

    The regression it guards stalled EVERY transition for hundreds of
    milliseconds, so the honest question is what a transition typically
    costs.  Judging the single worst frame instead put the guard on the one
    statistic machine noise owns: a run whose four transitions cost 79, 91,
    92 and 628ms is a machine hiccup on one pass, not a pipeline that stalls
    on every clip change."""
    noise_on_one_pass = [[1.0, 78.7], [1.0, 90.6], [1.0, 91.7], [1.0, 628.0]]
    stalls_every_pass = [[1.0, 640.0], [1.0, 647.0], [1.0, 651.0], [1.0, 660.0]]

    assert stall_per_transition(noise_on_one_pass) < 150.0
    assert stall_per_transition(stalls_every_pass) > 150.0


def test_a_status_read_that_caught_the_file_mid_replace_is_read_again():
    """A Funestra publishes its status by replacing the file, and a read that lands
    inside the replace gets the reader's empty record: no clip, not paused, about
    a Funestra that has a clip and is paused."""
    published = MainFunestraStatus(video="C:/example/scene one.mp4", duration_ms=61_000, paused=True)
    reads = iter([MainFunestraStatus(), published])

    assert published_status(lambda path: next(reads), Path("main_player_status.txt")) == published


def test_a_duration_read_refused_mid_replace_is_read_again(session, monkeypatch):
    """Windows can refuse a read outright while the Funestra replaces its status
    file, and the refusal went straight up into the test that asked."""
    session.config.main_funestra_status_file.parent.mkdir(parents=True, exist_ok=True)
    session.config.main_funestra_status_file.write_text("", encoding="utf-8")
    refusals = iter([PermissionError(13, "Permission denied")])

    def read(path):
        for refusal in refusals:
            raise refusal
        return {"video": "C:/example/scene one.mp4", "duration_ms": "61000"}

    monkeypatch.setattr(funestra_status, "read_key_values", read)

    assert session.read_main_funestra_status().duration_ms == 61_000


def test_a_replace_that_outlasts_one_retry_is_still_waited_out():
    published = MainFunestraStatus(video="C:/example/scene two.mp4", duration_ms=45_000, paused=True)
    reads = iter([MainFunestraStatus(), MainFunestraStatus(), MainFunestraStatus(), published])

    assert published_status(lambda path: next(reads), Path("main_player_status.txt")) == published


def test_a_record_that_stays_empty_comes_back_once_its_wait_is_spent():
    """A Funestra that has published nothing yet is not waited on forever, and the
    reads are paced: a reader holding the file open is what fails the replace."""
    clock = [0.0]
    reads = []

    def read(path):
        assert len(reads) < 1000, "reading as fast as it can instead of pacing its reads"
        reads.append(path)
        return MainFunestraStatus()

    def sleep(seconds):
        clock[0] += seconds

    empty = published_status(read, Path("main_player_status.txt"), budget_s=1.0,
                             now=lambda: clock[0], sleep=sleep)

    assert empty == MainFunestraStatus()
    assert clock[0] >= 1.0


class TestPostingADashboardCommand:
    """The harness presses the Dash's buttons, so it has to press them the way
    the Dash does.  It replaced the command file outright instead, which cost a
    run two ways: a press landing inside the bridge's ~20x/s drain was refused
    by Windows and the refusal went straight up into the test that asked, and a
    press landing on one still queued wiped it.  `dashboard_app` has used
    `append_command` -- which retries the refusal and queues rather than
    replaces -- all along.
    """

    def test_a_press_joins_one_still_queued_rather_than_wiping_it(self, session):
        session.write_dashboard_command("main_kino_activate")
        session.write_dashboard_command("genau_activate")

        queued = session.dashboard_cmd_file.read_text(encoding="utf-8").split()
        assert queued == ["main_kino_activate", "genau_activate"]

    def test_a_press_refused_mid_drain_is_pressed_again(self, session, monkeypatch):
        """Windows refuses the open outright while the bridge rewrites the file."""
        refusals = iter([PermissionError(13, "Permission denied")])
        real_open = Path.open

        def opening(self, *args, **kwargs):
            for refusal in refusals:
                raise refusal
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", opening)
        session.write_dashboard_command("main_kino_activate")

        assert session.dashboard_cmd_file.read_text(encoding="utf-8").split() == [
            "main_kino_activate"]


def test_a_wait_that_runs_out_describes_how_things_stood_when_it_gave_up(session):
    stood = {"then": "as it began"}

    def not_yet() -> bool:
        stood["then"] = "as it gave up"
        return False

    with pytest.raises(AssertionError, match="as it gave up"):
        session.wait_until(not_yet, timeout=0.01, description=lambda: stood["then"])


def _module_scoped_fixtures(tree: ast.Module) -> set[str]:
    return {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        and any(isinstance(d, ast.Call) and any(
            k.arg == "scope" and getattr(k.value, "value", None) == "module" for k in d.keywords)
            for d in node.decorator_list)}


def _tests_left_to_a_reaped_shared_session(source: str) -> list[str]:
    tree = ast.parse(source)
    shared = _module_scoped_fixtures(tree)
    tests = [(node.name, bool(shared & {a.arg for a in node.args.args}))
             for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    first_on_its_own = next((i for i, (_name, on_shared) in enumerate(tests) if not on_shared),
                            len(tests))
    return [name for name, on_shared in tests[first_on_its_own:] if on_shared]


def test_a_modules_shared_session_is_done_with_before_a_test_starts_one_of_its_own():
    for module in sorted((Path(__file__).parent / "integration").glob("test_*.py")):
        assert _tests_left_to_a_reaped_shared_session(
            module.read_text(encoding="utf-8")) == [], module.name


def test_every_app_a_run_stands_in_for_is_python_that_parses():
    """A stub app is Python inside a string, so nothing compiles it until a real
    run launches it -- and a stub that dies on import reads as the session waiting
    out an app that never answers, which is a slow run each time.  One escape too
    few, in a string holding a string, cost two of them (2026-10-06).
    """
    sources = []
    for path in sorted((Path(__file__).resolve().parent / "integration").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            named = getattr(node.targets[0], "id", "")
            if not named.endswith("_MAIN"):
                continue
            value = node.value
            literal = ast.literal_eval(
                value.args[0] if isinstance(value, ast.Call) else value)
            sources.append((f"{path.name}:{named}", textwrap.dedent(literal)))

    assert sources, "no stub app sources found to check"
    for where, source in sources:
        compile(source, where, "exec")


_WHAT_RUNS_THE_RUN = frozenset({
    "commands.py", "coverage_map.py", "fix_session_notice.py", "fix_sessions.py",
    "flake_gate_install.py", "hidden_desktop.py", "line_record.py", "main_verifier.py",
    "run_clock.py", "session_lock.py"})
_WALL_READINGS = frozenset({"monotonic", "time", "perf_counter"})
_TIMED_WAITS = frozenset({"wait", "join", "communicate", "run"})


def _reads_the_wall(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _WALL_READINGS
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "time")


def _counts_the_runs_seconds(timeout: ast.AST) -> bool:
    return (isinstance(timeout, ast.Call) and getattr(timeout.func, "id", "") == "min"
            and any(isinstance(bound, ast.Call) and getattr(bound.func, "attr", "") == "left"
                    for bound in timeout.args))


def _timeout_of(call: ast.Call, name: str) -> ast.AST | None:
    given = next((word.value for word in call.keywords if word.arg == "timeout"), None)
    first = call.args[0] if call.args else None
    if given is None and name in ("wait", "communicate"):
        return first
    if given is None and name == "join" and isinstance(getattr(first, "value", None), (int, float)):
        return first
    return given


def _sets_or_checks_a_wall_deadline(node: ast.AST) -> bool:
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, ast.Add) and (_reads_the_wall(node.left) or _reads_the_wall(node.right))
    return isinstance(node, ast.Compare) and any(
        _reads_the_wall(part) for side in (node.left, *node.comparators) for part in ast.walk(side))


def _waits_on_the_wall(tree: ast.AST) -> list[int]:
    found = []
    for node in ast.walk(tree):
        if _sets_or_checks_a_wall_deadline(node):
            found.append(node.lineno)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            timeout = _timeout_of(node, name) if name in _TIMED_WAITS else None
            if name == "wait_for_window_by_title" or (
                    timeout is not None and not _counts_the_runs_seconds(timeout)):
                found.append(node.lineno)
    return found


def test_no_check_waits_in_the_walls_seconds():
    integration = Path(integration_support.__file__).parent
    waits = {path.name: lines for path in sorted(integration.glob("*.py"))
             if path.name not in _WHAT_RUNS_THE_RUN
             and (lines := _waits_on_the_wall(ast.parse(path.read_text(encoding="utf-8"))))}

    assert waits == {}, (
        "these lines wait in the wall's seconds, which a run that other work starves spends "
        f"without running; wait through seen_within and the helpers built on it: {waits}")


def _calls_os(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            and node.func.attr == name)


def _clips_made_in(function: ast.AST) -> set[str]:
    return {target.id for node in ast.walk(function)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", "") == "clip_of_a_test_pattern"
            for target in node.targets if isinstance(target, ast.Name)}


def _links_or_dates_what_it_did_not_make(tree: ast.AST) -> list[int]:
    found = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        made = _clips_made_in(function)
        found |= {node.lineno for node in ast.walk(function)
                  if _calls_os(node, "link") or (_calls_os(node, "utime") and not (
                      node.args and isinstance(node.args[0], ast.Name)
                      and node.args[0].id in made))}
    return sorted(found)


def test_no_check_links_a_file_or_dates_any_but_a_clip_it_made():
    integration = Path(integration_support.__file__).parent
    reaches = {path.name: lines for path in sorted(integration.glob("*.py"))
               if path.name != "integration_support.py"
               and (lines := _links_or_dates_what_it_did_not_make(
                   ast.parse(path.read_text(encoding="utf-8"))))}

    assert reaches == {}, (
        "a hard link to a library clip shares that clip's dates, so a date set through one "
        "rewrites his library; date only a clip the check made with clip_of_a_test_pattern: "
        f"{reaches}")
