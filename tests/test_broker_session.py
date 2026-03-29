from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from fun_time.broker_session import BrokerSerialSession, SessionRetryState


class FakeAutoMode:
    def __init__(self, *, active: bool = False):
        self.active = active
        self.handle_line_calls: list[str] = []
        self.set_auto_calls: list[tuple[object, bool, str | None]] = []
        self.set_enabled_calls: list[tuple[object, bool]] = []

    @property
    def is_active(self) -> bool:
        return self.active

    def handle_line(self, _sock, line: str) -> None:
        self.handle_line_calls.append(line)

    def set_auto(self, sock, value: bool, mode_value: str | None = None) -> None:
        self.set_auto_calls.append((sock, value, mode_value))
        self.active = value

    def set_enabled(self, sock, value: bool) -> None:
        self.set_enabled_calls.append((sock, value))


def _build_session(*, auto_active: bool = False, monotonic=lambda: 10.0, activity_file=None, time_time=None):
    auto_mode = FakeAutoMode(active=auto_active)
    logger = MagicMock()
    kwargs = {}
    if activity_file is not None:
        kwargs["activity_file"] = activity_file
    if time_time is not None:
        kwargs["time_time"] = time_time
    session = BrokerSerialSession(
        serial_factory=MagicMock(),
        virtual_port="COM15",
        real_port="COM4",
        baud=115200,
        broker_cmd_file=Path("broker.cmd"),
        robot_hand_enabled_file=Path("robot_hand_enabled.txt"),
        auto_stale_timeout=2.0,
        stop_event=threading.Event(),
        broker_paused=threading.Event(),
        auto_mode=auto_mode,
        logger=logger,
        start_thread=MagicMock(),
        consume_command=lambda _path: None,
        read_robot_hand_enabled=lambda _path: True,
        monotonic=monotonic,
        **kwargs,
    )
    return session, auto_mode, logger


def test_handle_broker_command_sets_pause_and_resume():
    session, _auto_mode, logger = _build_session()

    session.handle_broker_command("PAUSE", object())
    assert session.broker_paused.is_set()
    logger.info.assert_called_once_with("OmniPause: broker paused")

    logger.reset_mock()
    session.handle_broker_command("RESUME", object())
    assert not session.broker_paused.is_set()
    logger.info.assert_called_once_with("OmniPause: broker resumed")


def test_handle_broker_command_toggles_robot_hand_enablement():
    session, auto_mode, logger = _build_session()
    sock = object()

    session.handle_broker_command("ROBOT_HAND_DISABLE", sock)
    session.handle_broker_command("ROBOT_HAND_ENABLE", sock)

    assert auto_mode.set_enabled_calls == [(sock, False), (sock, True)]
    logger.info.assert_not_called()


def test_sync_robot_hand_enabled_reads_shared_file_state():
    session, auto_mode, _logger = _build_session()
    session.read_robot_hand_enabled = lambda _path: False
    sock = object()

    session.sync_robot_hand_enabled(sock)

    assert auto_mode.set_enabled_calls == [(sock, False)]


def test_maybe_disable_stale_auto_turns_off_auto_when_stale():
    session, auto_mode, logger = _build_session(auto_active=True, monotonic=lambda: 10.0)
    session.last_real_rx_time = 7.0
    sock = object()

    session.maybe_disable_stale_auto(sock)

    assert auto_mode.set_auto_calls == [(sock, False, None)]
    logger.warning.assert_called_once_with("AUTO stale timeout reached after %.2fs", 2.0)


def test_maybe_disable_stale_auto_skips_when_paused_or_not_stale():
    session, auto_mode, logger = _build_session(auto_active=True, monotonic=lambda: 10.0)
    session.broker_paused.set()
    session.last_real_rx_time = 1.0

    session.maybe_disable_stale_auto(object())

    assert auto_mode.set_auto_calls == []
    logger.warning.assert_not_called()


def test_forward_real_to_virtual_updates_timestamp_and_handles_lines():
    session, auto_mode, _logger = _build_session(monotonic=lambda: 12.5)
    session_stop = threading.Event()
    retry_state = SessionRetryState()
    udp_sock = object()

    class FakeReal:
        def __init__(self):
            self.in_waiting = 1
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            session_stop.set()
            return b"hello\r\n"

    class FakeVirt:
        def __init__(self):
            self.writes: list[bytes] = []

        def write(self, data: bytes):
            self.writes.append(data)

    real = FakeReal()
    virt = FakeVirt()

    session.forward_real_to_virtual(real, virt, udp_sock, session_stop, retry_state)

    assert session.last_real_rx_time == 12.5
    assert virt.writes == [b"hello\r\n"]
    assert auto_mode.handle_line_calls == ["hello"]
    assert retry_state.value is False


def test_forward_virtual_to_real_skips_writes_while_auto_is_active():
    session, _auto_mode, _logger = _build_session(auto_active=True)
    session_stop = threading.Event()
    retry_state = SessionRetryState()

    class FakeVirt:
        def __init__(self):
            self.in_waiting = 1
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            session_stop.set()
            return b"ABC"

    class FakeReal:
        def __init__(self):
            self.writes: list[bytes] = []

        def write(self, data: bytes):
            self.writes.append(data)

    real = FakeReal()
    virt = FakeVirt()

    session.forward_virtual_to_real(virt, real, session_stop, retry_state)

    assert real.writes == []
    assert retry_state.value is False


def test_activity_file_written_on_real_rx(tmp_path):
    activity = tmp_path / "broker_activity.txt"
    wall_clock = [1000.0]
    session, auto_mode, _logger = _build_session(
        monotonic=lambda: 12.5,
        activity_file=activity,
        time_time=lambda: wall_clock[0],
    )
    session.activity_write_interval = 0.0  # no throttle for this test

    session_stop = threading.Event()
    retry_state = SessionRetryState()

    class FakeReal:
        def __init__(self):
            self.in_waiting = 1

        def read(self, _size):
            session_stop.set()
            return b"data\r\n"

    class FakeVirt:
        def write(self, _data):
            pass

    session.forward_real_to_virtual(FakeReal(), FakeVirt(), object(), session_stop, retry_state)

    assert activity.exists()
    assert float(activity.read_text(encoding="utf-8").strip()) == 1000.0


def test_activity_file_throttled(tmp_path):
    activity = tmp_path / "broker_activity.txt"
    wall_clock = [1000.0]
    session, _auto_mode, _logger = _build_session(
        monotonic=lambda: 12.5,
        activity_file=activity,
        time_time=lambda: wall_clock[0],
    )
    session.activity_write_interval = 10.0

    session._maybe_write_activity()
    assert activity.exists()
    first_write = activity.read_text(encoding="utf-8").strip()

    # Advance only 5 seconds — should NOT rewrite
    wall_clock[0] = 1005.0
    session._maybe_write_activity()
    assert activity.read_text(encoding="utf-8").strip() == first_write

    # Advance past interval — should rewrite
    wall_clock[0] = 1011.0
    session._maybe_write_activity()
    assert float(activity.read_text(encoding="utf-8").strip()) == 1011.0


def test_activity_file_written_on_virtual_tx(tmp_path):
    """Activity file is written when data flows from virtual port to real device."""
    activity = tmp_path / "broker_activity.txt"
    wall_clock = [1000.0]
    session, _auto_mode, _logger = _build_session(
        auto_active=False,
        activity_file=activity,
        time_time=lambda: wall_clock[0],
    )
    session.activity_write_interval = 0.0  # no throttle

    session_stop = threading.Event()
    retry_state = SessionRetryState()

    class FakeVirt:
        def __init__(self):
            self.in_waiting = 1

        def read(self, _size):
            session_stop.set()
            return b"L0P100\n"

    class FakeReal:
        def write(self, _data):
            pass

    session.forward_virtual_to_real(FakeVirt(), FakeReal(), session_stop, retry_state)

    assert activity.exists()
    assert float(activity.read_text(encoding="utf-8").strip()) == 1000.0


def test_no_activity_file_when_not_configured():
    session, _auto_mode, _logger = _build_session()
    # Should not raise even though activity_file is None
    session._maybe_write_activity()
