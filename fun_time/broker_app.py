from __future__ import annotations

import argparse
import logging
import re
import socket
import threading
import time
from pathlib import Path

import serial

from .broker_protocol import BrokerAutoController, parse_auto_transition
from .config import load_config
from .logging_utils import configure_logging, install_exception_logging
from .runtime_support import consume_command_file as _consume_command_file
from .runtime_support import preparse_config_path
from .threading_utils import start_daemon_thread

SERIAL_RETRY_DELAY_SECONDS = 1.0
RE_COM0COM_PORT = re.compile(r"COM0COM\\PORT\\(CNC[AB])(\d+)", re.IGNORECASE)


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


def udp_send(sock: socket.socket, host: str, port: int, msg: str) -> None:
    sock.sendto(msg.encode("utf-8"), (host, port))


def _iter_serial_ports():
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return list(list_ports.comports())


def _mfp_config_path(config) -> Path:
    return config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")


def _read_mfp_selected_serial_port(config) -> str | None:
    try:
        mfp_config_path = _mfp_config_path(config)
        if not mfp_config_path.exists():
            return None
        text = mfp_config_path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = re.search(r'"SelectedSerialPort"\s*:\s*"([^"]+)"', text)
    if not match:
        return None
    return match.group(1)


def _collect_com0com_ports() -> dict[str, tuple[str, str]]:
    ports: dict[str, tuple[str, str]] = {}
    for port in _iter_serial_ports():
        device = getattr(port, "device", None)
        if not device:
            continue
        desc = str(getattr(port, "description", "") or "")
        hwid = str(getattr(port, "hwid", "") or "")
        if "com0com" not in desc.lower() and "COM0COM\\PORT\\" not in hwid.upper():
            continue
        ports[str(device).upper()] = (desc, hwid)
    return ports


def resolve_virtual_port(config, configured_port: str, logger: logging.Logger) -> str:
    normalized = configured_port.upper()
    com0com_ports = _collect_com0com_ports()
    if normalized in com0com_ports:
        return configured_port

    if not com0com_ports:
        logger.warning("Configured virtual port %s is missing and no com0com ports were detected", configured_port)
        return configured_port

    mfp_selected = _read_mfp_selected_serial_port(config)
    if mfp_selected:
        match = RE_COM0COM_PORT.search(mfp_selected)
        if match:
            expected_role = "CNCB" if match.group(1).upper() == "CNCA" else "CNCA"
            expected_index = match.group(2)
            for device, (_desc, hwid) in com0com_ports.items():
                hwid_match = RE_COM0COM_PORT.search(hwid)
                if hwid_match and hwid_match.group(1).upper() == expected_role and hwid_match.group(2) == expected_index:
                    logger.warning(
                        "Configured virtual port %s is missing; using %s inferred from MFP serial port %s",
                        configured_port,
                        device,
                        mfp_selected,
                    )
                    return device

    cncb_devices: list[str] = []
    for device, (_desc, hwid) in com0com_ports.items():
        hwid_match = RE_COM0COM_PORT.search(hwid)
        if hwid_match and hwid_match.group(1).upper() == "CNCB":
            cncb_devices.append(device)

    if len(cncb_devices) == 1:
        logger.warning(
            "Configured virtual port %s is missing; using sole detected com0com broker-side port %s",
            configured_port,
            cncb_devices[0],
        )
        return cncb_devices[0]

    logger.warning(
        "Configured virtual port %s is missing; detected com0com ports=%s",
        configured_port,
        ", ".join(sorted(com0com_ports)),
    )
    return configured_port


def is_retryable_serial_error(exc: BaseException) -> bool:
    return isinstance(exc, (serial.SerialException, PermissionError, OSError))


def main(argv: list[str] | None = None) -> int:
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.broker", config.log_file("broker"))
    install_exception_logging(logger)
    args = build_parser(config).parse_args(argv)
    args.virtual_port = resolve_virtual_port(config, args.virtual_port, logger)

    state_file = Path(args.state_file)
    broker_cmd_file = Path(args.broker_cmd_file)
    state = {
        "last_real_rx_time": 0.0,
    }
    stop_event = threading.Event()
    broker_paused = threading.Event()
    auto_mode = BrokerAutoController(
        state_file=state_file,
        udp_host=args.udp_host,
        udp_port=args.udp_port,
        logger=logger,
        write_mode=write_mode,
        udp_send=udp_send,
    )

    def run_serial_session(udp_sock: socket.socket) -> bool:
        session_stop = threading.Event()
        retry_session = {"value": False}

        def forward_real_to_virtual(real, virt) -> None:
            buf = bytearray()
            while not stop_event.is_set() and not session_stop.is_set():
                try:
                    data = real.read(real.in_waiting or 1)
                    if not data:
                        continue

                    state["last_real_rx_time"] = time.monotonic()
                    virt.write(data)

                    buf.extend(data)
                    while b"\n" in buf:
                        raw_line, _, rest = buf.partition(b"\n")
                        buf[:] = rest
                        line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
                        if line:
                            auto_mode.handle_line(udp_sock, line)
                except Exception as exc:
                    logger.exception("REAL->VIRT error")
                    retry_session["value"] = is_retryable_serial_error(exc)
                    session_stop.set()
                    return

        def forward_virtual_to_real(virt, real) -> None:
            while not stop_event.is_set() and not session_stop.is_set():
                try:
                    data = virt.read(virt.in_waiting or 1)
                    if not data:
                        continue
                    if not auto_mode.is_active:
                        real.write(data)
                except Exception as exc:
                    logger.exception("VIRT->REAL error")
                    retry_session["value"] = is_retryable_serial_error(exc)
                    session_stop.set()
                    return

        thread_real: threading.Thread | None = None
        thread_virtual: threading.Thread | None = None
        try:
            with serial.Serial(args.virtual_port, args.baud, timeout=0.02) as virt, serial.Serial(
                args.real_port,
                args.baud,
                timeout=0.02,
            ) as real:
                state["last_real_rx_time"] = 0.0
                thread_real = start_daemon_thread(
                    target=forward_real_to_virtual,
                    args=(real, virt),
                    name="broker-real",
                )
                thread_virtual = start_daemon_thread(
                    target=forward_virtual_to_real,
                    args=(virt, real),
                    name="broker-virtual",
                )

                while not stop_event.is_set() and not session_stop.is_set():
                    time.sleep(0.2)
                    cmd = consume_broker_cmd(broker_cmd_file)
                    if cmd == "PAUSE":
                        broker_paused.set()
                        logger.info("OmniPause: broker paused")
                    elif cmd == "RESUME":
                        broker_paused.clear()
                        logger.info("OmniPause: broker resumed")
                    last_rx = float(state["last_real_rx_time"])
                    if auto_mode.is_active and not broker_paused.is_set() and last_rx and (time.monotonic() - last_rx > args.auto_stale_timeout):
                        logger.warning("AUTO stale timeout reached after %.2fs", args.auto_stale_timeout)
                        auto_mode.set_auto(udp_sock, False, mode_value="2")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.exception("Failed to open or run serial session")
            retry_session["value"] = is_retryable_serial_error(exc)
        finally:
            session_stop.set()
            if thread_real is not None:
                thread_real.join(timeout=1.0)
            if thread_virtual is not None:
                thread_virtual.join(timeout=1.0)

        return retry_session["value"]

    write_mode(state_file, "0", logger)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    logger.info("Starting broker: %s <-> %s", args.virtual_port, args.real_port)
    logger.info("Robot Hand UDP target: %s:%s", args.udp_host, args.udp_port)

    try:
        while not stop_event.is_set():
            should_retry = run_serial_session(udp_sock)
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
