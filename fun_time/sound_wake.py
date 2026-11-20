from __future__ import annotations

import socket
from pathlib import Path

from app_support.file_channel import publish_whole

from .loopback_inbox import post_to_inbox

SOUND_PORT_FILENAME = "audio_companion_port.txt"
READ_THE_FILES_AGAIN = "READ_THE_FILES_AGAIN"
READ_THE_FILES_AT_LEAST_EVERY_S = 1.0


def listen_for_the_session(host: str, port: int, state_dir: Path) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))
    sock.settimeout(READ_THE_FILES_AT_LEAST_EVERY_S)
    publish_whole(Path(state_dir) / SOUND_PORT_FILENAME, str(sock.getsockname()[1]))
    return sock


def tell_the_sound(state_dir: str | Path) -> None:
    post_to_inbox(Path(state_dir) / SOUND_PORT_FILENAME, READ_THE_FILES_AGAIN)
