"""COM-level broker tests.

These tests wire up the real BrokerAutoController and BrokerSerialSession
together with mocked serial (COM) ports, verifying the full flow from
serial data arriving on the device port through protocol parsing to
state-file writes and UDP message emission.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fun_time.broker_protocol import BrokerAutoController
from fun_time.broker_session import BrokerSerialSession


# ---------------------------------------------------------------------------
# Fake serial port
# ---------------------------------------------------------------------------

class FakeSerialPort:
    """Simulates a pyserial ``Serial`` object with separate rx/tx buffers.

    * ``inject()`` feeds the *rx* buffer (what ``read()`` returns).
    * ``write()`` appends to the *tx* buffer (what the session sends out).
    * ``tx_data`` lets the test inspect everything written by the session.
    """

    def __init__(self):
        self._rx_buf = bytearray()
        self._tx_buf = bytearray()
        self._lock = threading.Lock()
        self._closed = False

    # -- test side: inject data that read() will return --

    def inject(self, data: bytes) -> None:
        with self._lock:
            self._rx_buf.extend(data)

    # -- test side: inspect data written by the session --

    @property
    def tx_data(self) -> bytes:
        with self._lock:
            return bytes(self._tx_buf)

    # -- pyserial read interface --

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._rx_buf)

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            chunk = bytes(self._rx_buf[:size])
            del self._rx_buf[:size]
            return chunk

    def write(self, data: bytes) -> None:
        with self._lock:
            self._tx_buf.extend(data)

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_stack(tmp_path: Path, *, enabled: bool = True):
    """Build a real AutoController + Session wired to fake serial ports."""
    state_file = tmp_path / "state" / "robot_hand_mode.txt"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    rh_enabled_file = tmp_path / "state" / "robot_hand_enabled.txt"
    broker_cmd_file = tmp_path / "state" / "broker_cmd.txt"

    writes: list[tuple[Path, str]] = []
    udp_messages: list[str] = []

    def capture_write(path, value, _logger):
        writes.append((path, value))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def capture_udp(_sock, _host, _port, msg):
        udp_messages.append(msg)

    logger = MagicMock()

    controller = BrokerAutoController(
        state_file=state_file,
        udp_host="127.0.0.1",
        udp_port=9001,
        logger=logger,
        write_mode=capture_write,
        udp_send=capture_udp,
        enabled=enabled,
    )

    real_port = FakeSerialPort()
    virt_port = FakeSerialPort()

    clock = _FakeClock(0.0)

    def serial_factory(port_name, _baud, timeout=0.02):
        return real_port if port_name == "COM4" else virt_port

    stop_event = threading.Event()
    broker_paused = threading.Event()

    session = BrokerSerialSession(
        serial_factory=serial_factory,
        virtual_port="COM15",
        real_port="COM4",
        baud=115200,
        broker_cmd_file=broker_cmd_file,
        robot_hand_enabled_file=rh_enabled_file,
        auto_stale_timeout=2.0,
        stop_event=stop_event,
        broker_paused=broker_paused,
        auto_mode=controller,
        logger=logger,
        start_thread=_start_real_thread,
        consume_command=lambda _path: None,
        read_robot_hand_enabled=lambda _path: enabled,
        monotonic=clock,
        sleep=lambda _s: None,
    )

    return _Stack(
        session=session,
        controller=controller,
        real_port=real_port,
        virt_port=virt_port,
        stop_event=stop_event,
        broker_paused=broker_paused,
        state_file=state_file,
        rh_enabled_file=rh_enabled_file,
        broker_cmd_file=broker_cmd_file,
        writes=writes,
        udp_messages=udp_messages,
        logger=logger,
        clock=clock,
    )


class _FakeClock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _Stack:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _start_real_thread(target, args, name):
    t = threading.Thread(target=target, args=args, name=name, daemon=True)
    t.start()
    return t


def _wait_until(predicate, *, timeout=2.0, poll=0.02):
    """Poll *predicate* until True or timeout."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll)
    raise AssertionError(f"Timed out waiting for predicate")


# ---------------------------------------------------------------------------
# Tests — full COM→protocol→state flow
# ---------------------------------------------------------------------------

class TestAutoTransitionFromSerial:
    """Device sends auto-mode lines on the real COM port; verify state file + UDP."""

    def test_auto_on_line_activates_robot_hand(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(b"Auto mode is on!\r\n")

        # Run session in background; stop after data is consumed.
        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: s.controller.is_active)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert s.state_file.read_text(encoding="utf-8") == "1"
        assert "AUTO 1" in s.udp_messages
        assert "SHOW" in s.udp_messages

    def test_auto_off_line_deactivates_robot_hand(self, tmp_path):
        s = _build_stack(tmp_path)
        # Start active, then receive OFF line.
        s.controller.set_auto(object(), True)
        s.writes.clear()
        s.udp_messages.clear()

        s.real_port.inject(b"Auto mode is off!\r\n")
        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: not s.controller.is_active)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert s.state_file.read_text(encoding="utf-8") == "0"
        assert "AUTO 0" in s.udp_messages
        assert "HIDE" in s.udp_messages

    def test_freemode_line_activates(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(b"freeMode is on!\r\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: s.controller.is_active)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert "AUTO 1" in s.udp_messages


class TestStrokeAndBpmFromSerial:
    """Device sends stroke/BPM data on the real COM port; verify UDP messages."""

    def test_stroke_and_pattern_parsed_from_serial_data(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(b"StrokeName: Pull, PatternDuration: 2.0\r\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: "STROKE Pull" in s.udp_messages)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert "STROKE Pull" in s.udp_messages
        assert "PATTERN 2.0" in s.udp_messages
        assert "SYNC" in s.udp_messages

    def test_bpm_and_beats_parsed_from_serial_data(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(b"bpm 120, beats 4\r\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: "BPM 120" in s.udp_messages)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert "BPM 120" in s.udp_messages
        assert "BEATS 4" in s.udp_messages

    def test_combined_stroke_and_bpm_on_one_line(self, tmp_path):
        s = _build_stack(tmp_path)
        line = b"StrokeName: Twist, PatternDuration: 1.5 bpm 90, beats 2 continue StrokeName:\r\n"
        s.real_port.inject(line)

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: "BEATS 2" in s.udp_messages)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert s.udp_messages == [
            "AUTO 1",
            "SHOW",
            "STROKE Twist",
            "PATTERN 1.5",
            "SYNC",
            "AUTO 1",
            "SHOW",
            "BPM 90",
            "BEATS 2",
            "SYNC",
        ]


class TestSerialForwarding:
    """Verify data flows between virtual and real COM ports correctly."""

    def test_real_data_forwarded_to_virtual(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(b"device says hello\r\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: len(s.virt_port.tx_data) > 0)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert b"device says hello" in s.virt_port.tx_data

    def test_virtual_data_forwarded_to_real_when_auto_inactive(self, tmp_path):
        s = _build_stack(tmp_path)
        s.virt_port.inject(b"L0100\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: len(s.real_port.tx_data) > 0)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert b"L0100" in s.real_port.tx_data

    def test_virtual_data_blocked_when_auto_active(self, tmp_path):
        s = _build_stack(tmp_path)
        # Activate auto mode first.
        s.controller.set_auto(object(), True)

        s.virt_port.inject(b"L0100\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        # Wait for virt→real thread to consume the input.
        _wait_until(lambda: s.virt_port.in_waiting == 0)
        s.stop_event.set()
        runner.join(timeout=2.0)

        # Data consumed from virt but NOT forwarded to real (auto is active).
        assert s.real_port.tx_data == b""


class TestRobotHandEnabledSuppression:
    """When Robot Hand is disabled, auto-mode still tracks internally but
    the effective state file and UDP show inactive."""

    def test_disabled_robot_hand_suppresses_state_file(self, tmp_path):
        s = _build_stack(tmp_path, enabled=False)
        s.real_port.inject(b"Auto mode is on!\r\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: s.controller.is_active)
        s.stop_event.set()
        runner.join(timeout=2.0)

        # Internal state is active, but effective state is suppressed.
        assert s.controller.is_active is True
        assert s.state_file.read_text(encoding="utf-8") == "0"
        assert "AUTO 0" in s.udp_messages
        assert "HIDE" in s.udp_messages

    def test_reenabling_while_auto_active_restores_visibility(self, tmp_path):
        s = _build_stack(tmp_path, enabled=False)
        sock = object()
        s.controller.set_auto(sock, True)

        # Now re-enable Robot Hand.
        s.controller.set_enabled(sock, True)

        assert s.state_file.read_text(encoding="utf-8") == "1"
        assert s.udp_messages[-2:] == ["AUTO 1", "SHOW"]


class TestBrokerCommands:
    """Verify broker command processing through the full session stack."""

    def test_pause_blocks_stale_timeout(self, tmp_path):
        s = _build_stack(tmp_path)
        sock = object()
        s.controller.set_auto(sock, True)
        s.session.last_real_rx_time = 1.0
        s.clock.t = 10.0  # Well past stale timeout.

        # Pause the broker.
        s.session.handle_broker_command("PAUSE", sock)
        s.session.maybe_disable_stale_auto(sock)

        # Auto should still be active because broker is paused.
        assert s.controller.is_active is True

    def test_resume_allows_stale_timeout(self, tmp_path):
        s = _build_stack(tmp_path)
        sock = object()
        s.controller.set_auto(sock, True)
        s.session.last_real_rx_time = 1.0
        s.clock.t = 10.0

        s.session.handle_broker_command("PAUSE", sock)
        s.session.handle_broker_command("RESUME", sock)
        s.session.maybe_disable_stale_auto(sock)

        # After resume, stale timeout should fire.
        assert s.controller.is_active is False

    def test_robot_hand_disable_command_suppresses_output(self, tmp_path):
        s = _build_stack(tmp_path)
        sock = object()
        s.controller.set_auto(sock, True)
        s.writes.clear()
        s.udp_messages.clear()

        s.session.handle_broker_command("ROBOT_HAND_DISABLE", sock)

        assert s.state_file.read_text(encoding="utf-8") == "0"
        assert "HIDE" in s.udp_messages


class TestMultiLineSerialBuffer:
    """Verify that partial and multi-line serial data is buffered correctly."""

    def test_partial_lines_buffered_until_newline(self, tmp_path):
        s = _build_stack(tmp_path)
        # Send a partial line, then complete it.
        s.real_port.inject(b"Auto mode")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        import time
        time.sleep(0.1)

        # Auto should NOT be active yet (no newline).
        assert s.controller.is_active is False

        # Now complete the line.
        s.real_port.inject(b" is on!\r\n")
        _wait_until(lambda: s.controller.is_active)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert s.controller.is_active is True

    def test_multiple_lines_in_single_read(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(
            b"StrokeName: Push, PatternDuration: 3.0\r\n"
            b"bpm 60, beats 8\r\n"
        )

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: "BEATS 8" in s.udp_messages)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert "STROKE Push" in s.udp_messages
        assert "PATTERN 3.0" in s.udp_messages
        assert "BPM 60" in s.udp_messages
        assert "BEATS 8" in s.udp_messages


class TestAutoTransitionSequence:
    """Test realistic sequences of auto on/off transitions through the COM layer."""

    def test_on_off_on_sequence(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(b"Auto mode is on!\r\n")

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: s.controller.is_active)

        # Now turn off.
        s.real_port.inject(b"Auto mode is off!\r\n")
        _wait_until(lambda: not s.controller.is_active)

        # And on again.
        s.real_port.inject(b"freeMode is on!\r\n")
        _wait_until(lambda: s.controller.is_active)

        s.stop_event.set()
        runner.join(timeout=2.0)

        # Verify the transition sequence (filter duplicate re-publishes).
        auto_msgs = [m for m in s.udp_messages if m.startswith("AUTO")]
        transitions = [auto_msgs[0]] + [
            m for i, m in enumerate(auto_msgs[1:], 1) if m != auto_msgs[i - 1]
        ]
        assert transitions == ["AUTO 1", "AUTO 0", "AUTO 1"]

    def test_stroke_data_interleaved_with_auto_transitions(self, tmp_path):
        s = _build_stack(tmp_path)
        s.real_port.inject(
            b"Auto mode is on!\r\n"
            b"StrokeName: Wave, PatternDuration: 1.0\r\n"
            b"bpm 140, beats 4\r\n"
        )

        runner = threading.Thread(target=s.session.run, args=(object(),), daemon=True)
        runner.start()
        _wait_until(lambda: "BEATS 4" in s.udp_messages)
        s.stop_event.set()
        runner.join(timeout=2.0)

        assert "AUTO 1" in s.udp_messages
        assert "STROKE Wave" in s.udp_messages
        assert "BPM 140" in s.udp_messages
        # AUTO 1 should appear before stroke data.
        auto_idx = s.udp_messages.index("AUTO 1")
        stroke_idx = s.udp_messages.index("STROKE Wave")
        assert auto_idx < stroke_idx
