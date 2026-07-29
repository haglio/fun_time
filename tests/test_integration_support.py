"""Unit tests for the integration test-support harness (teardown safety).

These guard the deterministic child-process cleanup that
``FunTimeIntegrationSession.stop()`` must perform.  ``stop()`` hard-terminates
the orchestrator, so the orchestrator's own graceful ``_shutdown_children()``
never runs and its children (the two satellites, plus Nau/Genau/dashboard/
audio) are orphaned.  The teardown must therefore kill them itself, by the
exact processes recorded in ``bridge_pids.ini`` — PID *and* creation time, so a
PID Windows has since recycled is recognized rather than shot.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from fun_time import windows_bridge_orchestrator
from fun_time.loopback_server import LOOPBACK_PORT
from fun_time.windows_bridge_orchestrator import ChildProcess
from tests.integration import integration_support
from tests.integration.integration_support import (
    INTEGRATION_CONFIG_NAME,
    FunTimeIntegrationSession,
    close_udp_sinks,
    isolate_shared_resources,
)


BROKER_TCODE_PORT = 50557
GENAU_INBOUND_PORT = 50555
AUDIO_COMPANION_PORT = 50556


def _the_users_config() -> tuple[dict, dict]:
    """The two configs as the user's own session has them, naming what the machine shares."""
    config = {
        "audio_companion": {"host": "127.0.0.1", "port": AUDIO_COMPANION_PORT},
        "paths": {
            "broker_tray_launcher": "../osr2_broker/launch_broker_tray.vbs",
            # A session may be pinned at the broker's own directory rather than
            # its own state dir — a branch session is — and a run copies the
            # config whole.
            "broker_state_dir": "C:/Users/Example/workspace/fun_time/state",
        },
        "voice_control": {"enabled": True, "device_name": "Brio"},
        "loopback_port": LOOPBACK_PORT,
        # Vestigial: fun_time stopped parsing this when Genau moved to its own
        # repo, but the section is still sitting in every config file written
        # before then — including the one a run copies.
        "genau": {"udp_host": "127.0.0.1", "udp_port": GENAU_INBOUND_PORT,
                  "notify_port": AUDIO_COMPANION_PORT, "status_hide_ms": 1200},
        # FunTimeVR's main player streams T-Code through this key — to the same
        # broker inlet Nau and Genau use.
        "vr": {"library_dirs": [], "tcode_udp_port": BROKER_TCODE_PORT},
    }
    genau_config = {
        "genau": {
            "udp_port": GENAU_INBOUND_PORT,
            "notify_host": "127.0.0.1",
            "notify_port": AUDIO_COMPANION_PORT,
            "tcode_udp_port": BROKER_TCODE_PORT,
        },
        "nau": {"tcode_udp_port": BROKER_TCODE_PORT},
    }
    return config, genau_config


@pytest.fixture
def isolated_ports():
    """The rewritten pair, with the run's sink ports released afterwards."""
    config, genau_config = _the_users_config()
    isolate_shared_resources(config, genau_config)
    try:
        yield config, genau_config
    finally:
        close_udp_sinks()


def _completed(stdout: str):
    class _Result:
        pass

    result = _Result()
    result.stdout = stdout
    return result


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
            "nau_pid": ChildProcess(201, 2010),
            "portrait_pid": ChildProcess(202, 2020),   # satellite
            "landscape_pid": ChildProcess(203, 2030),  # satellite
            "dashboard_pid": ChildProcess(0, 0),       # disabled in integration — absent
            "genau_pid": ChildProcess(205, 2050),
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


def test_stop_does_not_kill_a_recorded_pid_windows_recycled(session):
    """Killing a recycled PID is how a run murders another run's pytest: the
    dead child's PID now belongs to somebody else."""
    _write_bridge_pids(session, {"genau_pid": ChildProcess(205, 2050)})
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "get_process_creation_time", return_value=9999), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()

    assert killed == []


def test_stop_survives_missing_bridge_pids(session):
    """A session that failed before writing bridge_pids.ini must still tear
    down without raising — and without trying to kill anything by PID."""
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()  # no bridge_pids.ini on disk

    assert killed == []


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
    with patch.object(integration_support.subprocess, "run") as run:
        run.return_value = _completed("0")
        session._wait_for_orchestrators_to_exit()

    ps_command = run.call_args.args[0][-1]
    assert "fun_time\\.orchestrator" in ps_command
    # The name appears regex-escaped, so match on its distinguishing stem.  What
    # matters is that the user's `--config fun_time_config.json` cannot match.
    assert INTEGRATION_CONFIG_NAME.removesuffix(".json") in ps_command


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


def test_the_integration_config_never_streams_tcode_to_the_machines_broker(isolated_ports):
    """T-Code is the one output that reaches hardware, and it leaves by UDP.

    Nau and Genau both stream to the broker's inlet, and the broker holds the
    OSR2's serial port — one process, one device, for the whole machine.  A run
    that keeps the production inlet therefore drives the user's OSR2 while they
    are using it, and no amount of desktop or state-dir isolation touches that:
    a socket is not per-desktop.  This is the leak the whole live-session guard
    existed to work around.

    Both senders move to one port together, so a future test can watch a run's
    own stream where it lands.
    """
    config, genau_config = isolated_ports

    assert genau_config["genau"]["tcode_udp_port"] != BROKER_TCODE_PORT
    assert genau_config["nau"]["tcode_udp_port"] == genau_config["genau"]["tcode_udp_port"]
    # The VR main player is the third sender at that inlet; it moves with them.
    assert config["vr"]["tcode_udp_port"] == genau_config["genau"]["tcode_udp_port"]


def test_the_runs_tcode_port_is_bound_so_the_stream_has_somewhere_to_land(isolated_ports):
    """Moving T-Code off the broker is not enough — it has to arrive somewhere.

    ``UdpTCodeSink.send`` is a bare ``sendto`` with no handler.  A datagram sent
    at a port nothing has bound draws an ICMP port-unreachable, which Windows
    hands back to the sender as WSAECONNRESET on a later call — so an unbound
    port would kill Genau's T-Code thread with an error the isolation invented.
    Binding it absorbs the stream the way the broker does.
    """
    _config, genau_config = isolated_ports
    port = genau_config["genau"]["tcode_udp_port"]

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rival:
        with pytest.raises(OSError):
            rival.bind(("127.0.0.1", port))


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
    machines_own = {AUDIO_COMPANION_PORT, GENAU_INBOUND_PORT, BROKER_TCODE_PORT, LOOPBACK_PORT}

    survivors = [
        value
        for value in _every_value(config) + _every_value(genau_config)
        if value in machines_own or str(value) in {str(port) for port in machines_own}
    ]

    assert survivors == []
