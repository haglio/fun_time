"""UDP listener that receives auto-mode state from the broker.

Replaces file-based polling of ``robot_hand_mode.txt`` with a direct
UDP signal, eliminating the multi-writer race condition.
"""
from __future__ import annotations

import socket
import threading


class AutoModeReceiver:
    """Thread-safe UDP receiver for AUTO messages from the broker.

    Bind to *port* (0 for ephemeral) on localhost.  Call :meth:`start`
    to begin listening; the broker sends ``AUTO 1`` / ``AUTO 0`` and
    this class exposes :attr:`auto_active` for the dispatch loop to read.
    """

    def __init__(self, *, port: int, initial: bool = False) -> None:
        self._auto_active = initial
        self._lock = threading.Lock()
        self._requested_port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._bound_port = 0

    @property
    def port(self) -> int:
        """The actual port after binding (useful when *port=0*)."""
        return self._bound_port

    @property
    def auto_active(self) -> bool:
        with self._lock:
            return self._auto_active

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", self._requested_port))
        self._sock.settimeout(0.2)
        self._bound_port = self._sock.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._listen, daemon=True, name="dispatch-udp"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _listen(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(256)
            except (socket.timeout, OSError):
                continue
            msg = data.decode("utf-8", errors="replace").strip()
            if msg == "AUTO 1":
                with self._lock:
                    self._auto_active = True
            elif msg == "AUTO 0":
                with self._lock:
                    self._auto_active = False
