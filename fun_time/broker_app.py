from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from pathlib import Path

import serial

from .broker_ports import resolve_virtual_port
from .broker_protocol import BrokerAutoController, parse_auto_transition
from .broker_session import BrokerSerialSession
from .config import load_config
from .logging_utils import configure_logging, install_exception_logging
from .runtime_support import consume_command_file as _consume_command_file
from .runtime_support import preparse_config_path
from .threading_utils import start_daemon_thread

SERIAL_RETRY_DELAY_SECONDS = 1.0


def _preparse_config(argv: list[str] | None) -> str | None:
    return preparse_config_path(argv)


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Broker between MFP virtual serial and the real OSR2.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--virtual-port", default=config.broker.virtual_port)
    ap.add_argument("--real-port", default=config.broker.real_port)
    ap.add_argument("--baud", type=int, default=config.broker.baud)
    ap.add_argument("--udp-host", default=config.broker.udp_host)
    ap.add_argument("--udp-port", type=int, default=config.broker.udp_port)
    ap.add_argument("--auto-stale-timeout", type=float, default=config.broker.auto_stale_timeout)
    ap.add_argument("--serial-retry-delay", type=float, default=SERIAL_RETRY_DELAY_SECONDS, help=argparse.SUPPRESS)
    ap.add_argument("--state-file", default=str(config.robot_hand_mode_file))
    ap.add_argument("--robot-hand-enabled-file", default=str(config.robot_hand_enabled_file))
    ap.add_argument("--broker-cmd-file", default=str(config.broker_cmd_file))
    return ap


def write_mode(path: Path, value: str, logger: logging.Logger) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except Exception:
        logger.exception("Failed to write mode file %s", path)


def consume_broker_cmd(path: Path) -> str | None:
    return _consume_command_file(path)


def read_robot_hand_enabled(path: Path) -> bool:
    try:
        if not path.exists():
            return True
        return path.read_text(encoding="utf-8").replace("\ufeff", "").strip() != "0"
    except Exception:
        return True


def ensure_robot_hand_enabled_file(path: Path, logger: logging.Logger) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or not path.read_text(encoding="utf-8").replace("\ufeff", "").strip():
            path.write_text("1", encoding="utf-8")
    except Exception:
        logger.exception("Failed to initialize Robot Hand enabled file %s", path)


def udp_send(sock: socket.socket, host: str, port: int, msg: str) -> None:
    sock.sendto(msg.encode("utf-8"), (host, port))


def is_retryable_serial_error(exc: BaseException) -> bool:
    return isinstance(exc, (serial.SerialException, PermissionError, OSError))


def main(argv: list[str] | None = None) -> int:
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.broker", config.log_file("broker"))
    install_exception_logging(logger)
    args = build_parser(config).parse_args(argv)
    args.virtual_port = resolve_virtual_port(config, args.virtual_port, logger)

    state_file = Path(args.state_file)
    robot_hand_enabled_file = Path(args.robot_hand_enabled_file)
    broker_cmd_file = Path(args.broker_cmd_file)
    ensure_robot_hand_enabled_file(robot_hand_enabled_file, logger)
    robot_hand_enabled = read_robot_hand_enabled(robot_hand_enabled_file)
    stop_event = threading.Event()
    broker_paused = threading.Event()
    auto_mode = BrokerAutoController(
        state_file=state_file,
        udp_host=args.udp_host,
        udp_port=args.udp_port,
        logger=logger,
        write_mode=write_mode,
        udp_send=udp_send,
        enabled=robot_hand_enabled,
    )
    session = BrokerSerialSession(
        serial_factory=serial.Serial,
        virtual_port=args.virtual_port,
        real_port=args.real_port,
        baud=args.baud,
        broker_cmd_file=broker_cmd_file,
        robot_hand_enabled_file=robot_hand_enabled_file,
        auto_stale_timeout=args.auto_stale_timeout,
        stop_event=stop_event,
        broker_paused=broker_paused,
        auto_mode=auto_mode,
        logger=logger,
        start_thread=start_daemon_thread,
        consume_command=consume_broker_cmd,
        read_robot_hand_enabled=read_robot_hand_enabled,
        monotonic=time.monotonic,
        sleep=time.sleep,
        is_retryable_error=is_retryable_serial_error,
    )

    write_mode(state_file, "0", logger)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    logger.info("Starting broker: %s <-> %s", args.virtual_port, args.real_port)
    logger.info("Robot Hand UDP target: %s:%s", args.udp_host, args.udp_port)

    try:
        while not stop_event.is_set():
            should_retry = session.run(udp_sock)
            if not should_retry or stop_event.is_set():
                break
            logger.warning("Retrying serial session in %.2fs", args.serial_retry_delay)
            time.sleep(args.serial_retry_delay)
    except KeyboardInterrupt:
        logger.info("Broker interrupted")
    finally:
        stop_event.set()
        udp_sock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
