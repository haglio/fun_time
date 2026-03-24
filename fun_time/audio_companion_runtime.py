from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class AudioCompanionRuntime:
    sock: Any
    controller: Any
    mode_file: Path
    read_mode_active: Callable[[Path], bool]
    paused_file: Path
    read_paused_state: Callable[[Path], bool]

    def receive_udp_line(self) -> str:
        try:
            data, _addr = self.sock.recvfrom(4096)
        except socket.timeout:
            return ""
        return data.decode("utf-8", errors="replace").strip()

    def process_iteration(self) -> None:
        line = self.receive_udp_line()
        self.controller.set_mode_active(self.read_mode_active(self.mode_file))
        self.controller.set_manual_paused(self.read_paused_state(self.paused_file))
        if line:
            self.controller.handle_udp_line(line)

    def run_forever(self) -> None:
        while True:
            self.process_iteration()

    def close(self) -> None:
        self.sock.close()
