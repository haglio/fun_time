from __future__ import annotations

import argparse
import logging
import re
import socket
import threading
import time
from pathlib import Path

import serial

from .config import load_config
from .logging_utils import configure_logging, install_exception_logging

RE_BPM = re.compile(r"\bbpm\s+(\d+),\s+beats\s+(\d+)", re.IGNORECASE)
RE_STROKE = re.compile(r"StrokeName:\s*([^,]+),\s*PatternDuration:\s*([0-9.]+)", re.IGNORECASE)


def _preparse_config(argv: list[str] | None) -> str | None:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--config")
    known, _ = ap.parse_known_args(argv)
    return known.config


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Broker between MFP virtual serial and the real OSR2.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--virtual-port", default=config.broker.virtual_port)
    ap.add_argument("--real-port", default=config.broker.real_port)
    ap.add_argument("--baud", type=int, default=config.broker.baud)
    ap.add_argument("--udp-host", default=config.broker.udp_host)
    ap.add_argument("--udp-port", type=int, default=config.broker.udp_port)
    ap.add_argument("--auto-stale-timeout", type=float, default=config.broker.auto_stale_timeout)
    ap.add_argument("--state-file", default=str(config.robot_hand_mode_file))
    return ap


def write_mode(path: Path, value: str, logger: logging.Logger) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except Exception:
        logger.exception("Failed to write mode file %s", path)


def udp_send(sock: socket.socket, host: str, port: int, msg: str) -> None:
    sock.sendto(msg.encode("utf-8"), (host, port))


def main(argv: list[str] | None = None) -> int:
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.broker", config.log_file("broker"))
    install_exception_logging(logger)
    args = build_parser(config).parse_args(argv)

    state_file = Path(args.state_file)
    state = {
        "last_real_rx_time": 0.0,
        "auto_active": False,
    }
    lock = threading.Lock()
    stop_event = threading.Event()

    def set_auto(sock: socket.socket, value: bool, mode_value: str | None = None) -> None:
        with lock:
            changed = state["auto_active"] != value
            state["auto_active"] = value

        mode_text = mode_value if mode_value is not None else ("1" if value else "0")
        write_mode(state_file, mode_text, logger)
        udp_send(sock, args.udp_host, args.udp_port, f"AUTO {1 if value else 0}")
        udp_send(sock, args.udp_host, args.udp_port, "SHOW" if value else "HIDE")

        if changed:
            logger.info("AUTO %s", "ON" if value else "OFF")

    def get_auto() -> bool:
        with lock:
            return bool(state["auto_active"])

    def handle_line(sock: socket.socket, line: str) -> None:
        low = line.lower()

        if "freemode is on" in low or "freemode tcode task started" in low:
            set_auto(sock, True)

        if "freemode is off" in low or "freemode tcode task is stopped" in low:
            set_auto(sock, False)

        stroke_match = RE_STROKE.search(line)
        if stroke_match:
            udp_send(sock, args.udp_host, args.udp_port, f"STROKE {stroke_match.group(1).strip()}")
            udp_send(sock, args.udp_host, args.udp_port, f"PATTERN {stroke_match.group(2)}")
            udp_send(sock, args.udp_host, args.udp_port, "SYNC")

        bpm_match = RE_BPM.search(line)
        if bpm_match:
            udp_send(sock, args.udp_host, args.udp_port, f"BPM {bpm_match.group(1)}")
            udp_send(sock, args.udp_host, args.udp_port, f"BEATS {bpm_match.group(2)}")

        if "continue strokename:" in low or "start transition" in low:
            udp_send(sock, args.udp_host, args.udp_port, "SYNC")

    def forward_real_to_virtual(real, virt, udp_sock: socket.socket) -> None:
        buf = bytearray()
        while not stop_event.is_set():
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
                        handle_line(udp_sock, line)
            except Exception:
                logger.exception("REAL->VIRT error")
                stop_event.set()
                return

    def forward_virtual_to_real(virt, real) -> None:
        while not stop_event.is_set():
            try:
                data = virt.read(virt.in_waiting or 1)
                if not data:
                    continue
                if not get_auto():
                    real.write(data)
            except Exception:
                logger.exception("VIRT->REAL error")
                stop_event.set()
                return

    write_mode(state_file, "0", logger)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    logger.info("Starting broker: %s <-> %s", args.virtual_port, args.real_port)
    logger.info("Robot Hand UDP target: %s:%s", args.udp_host, args.udp_port)

    try:
        with serial.Serial(args.virtual_port, args.baud, timeout=0.02) as virt, serial.Serial(
            args.real_port,
            args.baud,
            timeout=0.02,
        ) as real:
            thread_real = threading.Thread(target=forward_real_to_virtual, args=(real, virt, udp_sock), daemon=True, name="broker-real")
            thread_virtual = threading.Thread(target=forward_virtual_to_real, args=(virt, real), daemon=True, name="broker-virtual")
            thread_real.start()
            thread_virtual.start()

            while not stop_event.is_set():
                time.sleep(0.2)
                last_rx = float(state["last_real_rx_time"])
                if get_auto() and last_rx and (time.monotonic() - last_rx > args.auto_stale_timeout):
                    logger.warning("AUTO stale timeout reached after %.2fs", args.auto_stale_timeout)
                    set_auto(udp_sock, False, mode_value="2")
    except KeyboardInterrupt:
        logger.info("Broker interrupted")
    finally:
        stop_event.set()
        udp_sock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())