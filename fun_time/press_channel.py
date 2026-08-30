"""How the dispatch loop tells the bar that one of its controls just took.

A hotkey or a voice phrase reaches the dispatch loop, not the dashboard, so the
control it names would flash for a click and not for the key that does the same
thing.  The loop sends the action id here as a datagram, and the bar flashes it
either way.  The port is the machine's to choose, so this end publishes it.

No Qt: a datagram lands on a worker thread and the GUI thread has to be told,
but WHICH way belongs to the window.
"""
from __future__ import annotations

import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path

# Read by windows_bridge_dispatch_loop, which int()s the text as it finds it.
PRESS_PORT_FILENAME = "dashboard_press_port.txt"

# An action id is a short word; nothing longer is a press.
_MAX_DATAGRAM = 256


class PressChannel:
    """The bar's end of that feed: one socket, one thread, one queue.

    *on_press* is called on the listener's thread each time a press lands, with
    no arguments — the window uses it to cross to the GUI thread, and then reads
    what arrived with :meth:`take_all`.
    """

    def __init__(self, state_dir: Path, on_press: Callable[[], None]) -> None:
        self._on_press = on_press
        self._queue: queue.Queue[str] = queue.Queue()
        self._stopping = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / PRESS_PORT_FILENAME).write_text(str(self.port), encoding="utf-8")
        threading.Thread(
            target=self._listen, daemon=True, name="press-listener").start()

    @property
    def port(self) -> int:
        """The port the machine gave this channel."""
        return int(self._sock.getsockname()[1])

    @property
    def listening(self) -> bool:
        """Whether the listener is still meant to be reading."""
        return not self._stopping.is_set()

    def take_all(self) -> list[str]:
        """Every press that has arrived since the last call, oldest first."""
        taken: list[str] = []
        while True:
            try:
                taken.append(self._queue.get_nowait())
            except queue.Empty:
                return taken

    def stop(self) -> None:
        """Wind the listener down; closing the socket is what unblocks it."""
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
                self._on_press()
            except OSError:
                break
