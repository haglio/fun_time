from __future__ import annotations

import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path

PRESS_PORT_FILENAME = "dashboard_press_port.txt"

_MAX_DATAGRAM = 4096


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
        try:
            self._sock.close()
        except OSError:
            pass

    def _listen(self) -> None:
        while self.listening:
            try:
                data, _ = self._sock.recvfrom(_MAX_DATAGRAM)
                self._queue.put(data.decode("utf-8").strip())
                self._on_arrival()
            except OSError:
                break


def post_to_inbox(port_file: Path, text: str) -> bool:
    try:
        port = int(port_file.read_text(encoding="utf-8"))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(text.encode("utf-8"), ("127.0.0.1", port))
    except (OSError, ValueError):
        return False
    return True
