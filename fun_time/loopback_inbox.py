from __future__ import annotations

import contextlib
import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from .read_when_changed import ReadWhenChanged

PRESS_PORT_FILENAME = "dashboard_press_port.txt"

_MAX_DATAGRAM = 65536


class LoopbackInbox:
    def __init__(self, port_file: Path, *, listener_name: str,
                 on_arrival: Callable[[], None] = lambda: None) -> None:
        self._on_arrival = on_arrival
        self._queue: queue.Queue[str] = queue.Queue()
        self._stopping = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(self.port), encoding="utf-8")
        threading.Thread(target=self._listen, daemon=True, name=listener_name).start()

    @property
    def port(self) -> int:
        return int(self._sock.getsockname()[1])

    @property
    def listening(self) -> bool:
        return not self._stopping.is_set()

    def take_all(self) -> list[str]:
        taken: list[str] = []
        while True:
            try:
                taken.append(self._queue.get_nowait())
            except queue.Empty:
                return taken

    def stop(self) -> None:
        self._stopping.set()
        with contextlib.suppress(OSError):
            self._sock.close()

    def _listen(self) -> None:
        while self.listening:
            try:
                data, _ = self._sock.recvfrom(_MAX_DATAGRAM)
                self._queue.put(data.decode("utf-8").strip())
                self._on_arrival()
            except OSError:
                break


def _port_in(port_file: Path) -> int | None:
    try:
        return int(port_file.read_text(encoding="utf-8"))
    except ValueError:
        return None


class InboxSender:
    def __init__(self, port_file: Path) -> None:
        self._port = ReadWhenChanged(port_file, _port_in)
        self._socket: socket.socket | None = None

    def post(self, text: str) -> bool:
        port = self._port.latest()
        if port is None:
            return False
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.sendto(text.encode("utf-8"), ("127.0.0.1", port))
        except OSError:
            return False
        return True


_SENDERS: dict[Path, InboxSender] = {}


def post_to_inbox(port_file: Path, text: str) -> bool:
    sender = _SENDERS.get(port_file)
    if sender is None:
        sender = _SENDERS.setdefault(port_file, InboxSender(port_file))
    return sender.post(text)
