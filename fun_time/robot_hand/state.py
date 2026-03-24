from __future__ import annotations

import logging
import socket
import threading
from dataclasses import dataclass, field


@dataclass
class SharedState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    auto_active: bool = False
    visible: bool = False
    raw_bpm: float | None = None
    beats: int | None = None
    stroke_name: str = ""
    pattern_duration: float | None = None
    sync_pulse_id: int = 0
    last_msg: str = ""
    error: str | None = None


def udp_reader(host: str, port: int, state: SharedState, stop_event: threading.Event, logger: logging.Logger) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    sock.settimeout(0.2)
    logger.info("Robot Hand UDP listener bound on %s:%s", host, port)

    try:
        while not stop_event.is_set():
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue

            line = data.decode("utf-8", errors="replace").strip()
            parts = line.split(" ", 1)
            cmd = parts[0].upper()
            arg = parts[1] if len(parts) > 1 else ""

            with state.lock:
                state.last_msg = line

                if cmd == "SHOW":
                    state.visible = True
                    logger.info("Received SHOW")
                elif cmd == "HIDE":
                    state.visible = False
                    logger.info("Received HIDE")
                elif cmd == "AUTO":
                    state.auto_active = arg.strip() == "1"
                    logger.info("Received AUTO %s", 1 if state.auto_active else 0)
                elif cmd == "BPM":
                    try:
                        state.raw_bpm = float(arg.strip())
                    except ValueError:
                        logger.warning("Invalid BPM payload: %s", line)
                elif cmd == "BEATS":
                    try:
                        state.beats = int(arg.strip())
                    except ValueError:
                        logger.warning("Invalid BEATS payload: %s", line)
                elif cmd == "STROKE":
                    state.stroke_name = arg.strip()
                elif cmd == "PATTERN":
                    try:
                        state.pattern_duration = float(arg.strip())
                    except ValueError:
                        logger.warning("Invalid PATTERN payload: %s", line)
                elif cmd == "SYNC":
                    state.sync_pulse_id += 1
    except Exception as exc:
        logger.exception("UDP reader failed")
        with state.lock:
            state.error = str(exc)
    finally:
        sock.close()
