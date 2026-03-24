from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionRetryState:
    value: bool = False


class BrokerSerialSession:
    def __init__(
        self,
        *,
        serial_factory,
        virtual_port: str,
        real_port: str,
        baud: int,
        broker_cmd_file: Path,
        robot_hand_enabled_file: Path,
        auto_stale_timeout: float,
        stop_event,
        broker_paused,
        auto_mode,
        logger,
        start_thread,
        consume_command,
        read_robot_hand_enabled,
        monotonic=time.monotonic,
        sleep=time.sleep,
        is_retryable_error=None,
    ):
        self.serial_factory = serial_factory
        self.virtual_port = virtual_port
        self.real_port = real_port
        self.baud = baud
        self.broker_cmd_file = broker_cmd_file
        self.robot_hand_enabled_file = robot_hand_enabled_file
        self.auto_stale_timeout = auto_stale_timeout
        self.stop_event = stop_event
        self.broker_paused = broker_paused
        self.auto_mode = auto_mode
        self.logger = logger
        self.start_thread = start_thread
        self.consume_command = consume_command
        self.read_robot_hand_enabled = read_robot_hand_enabled
        self.monotonic = monotonic
        self.sleep = sleep
        self.is_retryable_error = is_retryable_error or (lambda _exc: False)
        self.last_real_rx_time = 0.0
        self.poll_interval_seconds = 0.05

    def run(self, udp_sock) -> bool:
        session_stop = threading.Event()
        retry_state = SessionRetryState()
        thread_real = None
        thread_virtual = None

        try:
            with self.serial_factory(self.virtual_port, self.baud, timeout=0.02) as virt, self.serial_factory(
                self.real_port,
                self.baud,
                timeout=0.02,
            ) as real:
                self.last_real_rx_time = 0.0
                thread_real = self.start_thread(
                    target=self.forward_real_to_virtual,
                    args=(real, virt, udp_sock, session_stop, retry_state),
                    name="broker-real",
                )
                thread_virtual = self.start_thread(
                    target=self.forward_virtual_to_real,
                    args=(virt, real, session_stop, retry_state),
                    name="broker-virtual",
                )

                while not self.stop_event.is_set() and not session_stop.is_set():
                    self.sleep(self.poll_interval_seconds)
                    self.tick_command_and_stale_timeout(udp_sock)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            self.logger.exception("Failed to open or run serial session")
            retry_state.value = self.is_retryable_error(exc)
        finally:
            session_stop.set()
            if thread_real is not None:
                thread_real.join(timeout=1.0)
            if thread_virtual is not None:
                thread_virtual.join(timeout=1.0)

        return retry_state.value

    def forward_real_to_virtual(self, real, virt, udp_sock, session_stop, retry_state: SessionRetryState) -> None:
        buf = bytearray()
        while not self.stop_event.is_set() and not session_stop.is_set():
            try:
                data = real.read(real.in_waiting or 1)
                if not data:
                    continue

                self.last_real_rx_time = self.monotonic()
                virt.write(data)

                buf.extend(data)
                while b"\n" in buf:
                    raw_line, _, rest = buf.partition(b"\n")
                    buf[:] = rest
                    line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
                    if line:
                        self.auto_mode.handle_line(udp_sock, line)
            except Exception as exc:
                self.logger.exception("REAL->VIRT error")
                retry_state.value = self.is_retryable_error(exc)
                session_stop.set()
                return

    def forward_virtual_to_real(self, virt, real, session_stop, retry_state: SessionRetryState) -> None:
        while not self.stop_event.is_set() and not session_stop.is_set():
            try:
                data = virt.read(virt.in_waiting or 1)
                if not data:
                    continue
                if not self.auto_mode.is_active:
                    real.write(data)
            except Exception as exc:
                self.logger.exception("VIRT->REAL error")
                retry_state.value = self.is_retryable_error(exc)
                session_stop.set()
                return

    def tick_command_and_stale_timeout(self, udp_sock) -> None:
        cmd = self.consume_command(self.broker_cmd_file)
        self.handle_broker_command(cmd, udp_sock)
        self.sync_robot_hand_enabled(udp_sock)
        self.maybe_disable_stale_auto(udp_sock)

    def handle_broker_command(self, cmd: str | None, udp_sock) -> None:
        if cmd == "PAUSE":
            self.broker_paused.set()
            self.logger.info("OmniPause: broker paused")
        elif cmd == "RESUME":
            self.broker_paused.clear()
            self.logger.info("OmniPause: broker resumed")
        elif cmd == "ROBOT_HAND_DISABLE":
            self.auto_mode.set_enabled(udp_sock, False)
        elif cmd == "ROBOT_HAND_ENABLE":
            self.auto_mode.set_enabled(udp_sock, True)

    def sync_robot_hand_enabled(self, udp_sock) -> None:
        enabled = self.read_robot_hand_enabled(self.robot_hand_enabled_file)
        self.auto_mode.set_enabled(udp_sock, enabled)

    def maybe_disable_stale_auto(self, udp_sock) -> None:
        if not self.auto_mode.is_active:
            return
        if self.broker_paused.is_set():
            return
        if not self.last_real_rx_time:
            return
        if self.monotonic() - self.last_real_rx_time <= self.auto_stale_timeout:
            return
        self.logger.warning("AUTO stale timeout reached after %.2fs", self.auto_stale_timeout)
        self.auto_mode.set_auto(udp_sock, False)
