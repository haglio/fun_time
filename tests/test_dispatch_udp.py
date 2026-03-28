"""Tests for the dispatch-loop UDP auto-mode listener."""
from __future__ import annotations

import socket
import time
import threading

from fun_time.dispatch_udp import AutoModeReceiver


def _send_udp(port: int, msg: str) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(msg.encode("utf-8"), ("127.0.0.1", port))
    sock.close()


class TestAutoModeReceiver:
    def test_starts_inactive(self):
        r = AutoModeReceiver(port=0)
        assert r.auto_active is False

    def test_receives_auto_on(self):
        r = AutoModeReceiver(port=0)
        r.start()
        try:
            _send_udp(r.port, "AUTO 1")
            deadline = time.monotonic() + 2.0
            while not r.auto_active and time.monotonic() < deadline:
                time.sleep(0.01)
            assert r.auto_active is True
        finally:
            r.stop()

    def test_receives_auto_off(self):
        r = AutoModeReceiver(port=0)
        r.start()
        try:
            _send_udp(r.port, "AUTO 1")
            deadline = time.monotonic() + 2.0
            while not r.auto_active and time.monotonic() < deadline:
                time.sleep(0.01)

            _send_udp(r.port, "AUTO 0")
            deadline = time.monotonic() + 2.0
            while r.auto_active and time.monotonic() < deadline:
                time.sleep(0.01)
            assert r.auto_active is False
        finally:
            r.stop()

    def test_ignores_non_auto_messages(self):
        r = AutoModeReceiver(port=0)
        r.start()
        try:
            _send_udp(r.port, "BPM 120")
            _send_udp(r.port, "SHOW")
            _send_udp(r.port, "STROKE Pull")
            time.sleep(0.1)
            assert r.auto_active is False
        finally:
            r.stop()

    def test_stop_is_idempotent(self):
        r = AutoModeReceiver(port=0)
        r.start()
        r.stop()
        r.stop()  # should not raise

    def test_port_zero_binds_ephemeral(self):
        r = AutoModeReceiver(port=0)
        r.start()
        try:
            assert r.port > 0
        finally:
            r.stop()

    def test_initial_value_can_be_set(self):
        r = AutoModeReceiver(port=0, initial=True)
        assert r.auto_active is True
