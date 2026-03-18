"""Tests for fun_time.robot_hand.state (SharedState + UDP message parsing)."""
from __future__ import annotations

import socket
import threading
import time
import logging

import pytest

from fun_time.robot_hand.state import SharedState, udp_reader


# ---------------------------------------------------------------------------
# SharedState defaults
# ---------------------------------------------------------------------------

class TestSharedState:
    def test_default_values(self):
        s = SharedState()
        assert s.auto_active is False
        assert s.visible is False
        assert s.raw_bpm is None
        assert s.beats is None
        assert s.stroke_name == ""
        assert s.pattern_duration is None
        assert s.sync_pulse_id == 0
        assert s.last_msg == ""
        assert s.error is None

    def test_has_lock(self):
        s = SharedState()
        assert isinstance(s.lock, threading.Lock)


# ---------------------------------------------------------------------------
# Helpers: send UDP from a test thread to the udp_reader bound on a free port
# ---------------------------------------------------------------------------

def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send(port: int, msg: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(msg.encode(), ("127.0.0.1", port))


def _run_reader(state: SharedState, port: int) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    logger = logging.getLogger("test.udp_reader")
    t = threading.Thread(target=udp_reader, args=("127.0.0.1", port, state, stop, logger), daemon=True)
    t.start()
    time.sleep(0.05)  # let the socket bind
    return t, stop


# ---------------------------------------------------------------------------
# UDP message parsing
# ---------------------------------------------------------------------------

class TestUdpReader:
    def _run_with_message(self, msg: str) -> SharedState:
        state = SharedState()
        port = _free_udp_port()
        t, stop = _run_reader(state, port)
        try:
            _send(port, msg)
            time.sleep(0.1)
        finally:
            stop.set()
            t.join(timeout=1.0)
        return state

    def test_show_sets_visible(self):
        state = self._run_with_message("SHOW")
        assert state.visible is True

    def test_hide_clears_visible(self):
        state = SharedState()
        state.visible = True
        port = _free_udp_port()
        t, stop = _run_reader(state, port)
        try:
            _send(port, "HIDE")
            time.sleep(0.1)
        finally:
            stop.set()
            t.join(timeout=1.0)
        assert state.visible is False

    def test_auto_1_enables_auto_and_visible(self):
        state = self._run_with_message("AUTO 1")
        assert state.auto_active is True
        assert state.visible is True

    def test_auto_0_disables_auto_and_visible(self):
        state = SharedState()
        state.auto_active = True
        state.visible = True
        port = _free_udp_port()
        t, stop = _run_reader(state, port)
        try:
            _send(port, "AUTO 0")
            time.sleep(0.1)
        finally:
            stop.set()
            t.join(timeout=1.0)
        assert state.auto_active is False
        assert state.visible is False

    def test_bpm_parsed(self):
        state = self._run_with_message("BPM 120.5")
        assert state.raw_bpm == pytest.approx(120.5)

    def test_bpm_invalid_does_not_crash(self):
        state = self._run_with_message("BPM notanumber")
        assert state.raw_bpm is None

    def test_beats_parsed(self):
        state = self._run_with_message("BEATS 4")
        assert state.beats == 4

    def test_beats_invalid_does_not_crash(self):
        state = self._run_with_message("BEATS oops")
        assert state.beats is None

    def test_stroke_stored(self):
        state = self._run_with_message("STROKE twist")
        assert state.stroke_name == "twist"

    def test_pattern_parsed(self):
        state = self._run_with_message("PATTERN 2.5")
        assert state.pattern_duration == pytest.approx(2.5)

    def test_pattern_invalid_does_not_crash(self):
        state = self._run_with_message("PATTERN bad")
        assert state.pattern_duration is None

    def test_sync_increments(self):
        state = SharedState()
        port = _free_udp_port()
        t, stop = _run_reader(state, port)
        try:
            _send(port, "SYNC")
            time.sleep(0.05)
            _send(port, "SYNC")
            time.sleep(0.1)
        finally:
            stop.set()
            t.join(timeout=1.0)
        assert state.sync_pulse_id == 2

    def test_last_msg_recorded(self):
        state = self._run_with_message("BPM 99")
        assert state.last_msg == "BPM 99"

    def test_unknown_command_is_ignored(self):
        # Must not raise; state remains at defaults
        state = self._run_with_message("UNKNOWN payload")
        # No crash is the assertion; also last_msg logged
        assert state.last_msg == "UNKNOWN payload"

    def test_stop_event_terminates_reader(self):
        state = SharedState()
        port = _free_udp_port()
        t, stop = _run_reader(state, port)
        stop.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
