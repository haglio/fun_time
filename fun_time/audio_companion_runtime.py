from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class AudioCompanionRuntime:
    sock: Any
    controller: Any
    cmd_file: Path
    consume_command_file: Callable[[Path], str | None]

    def receive_udp_line(self) -> str:
        try:
            data, _addr = self.sock.recvfrom(4096)
        except socket.timeout:
            return ""
        return data.decode("utf-8", errors="replace").strip()

    def apply_runtime_command(self, command: str | None) -> None:
        if command == "PAUSE":
            self.controller.set_manual_paused(True)
        elif command == "RESUME":
            self.controller.set_manual_paused(False)

    def process_iteration(self) -> None:
        line = self.receive_udp_line()
        self.apply_runtime_command(self.consume_command_file(self.cmd_file))
        if line:
            self.controller.handle_udp_line(line)

    def run_forever(self) -> None:
        while True:
            self.process_iteration()

    def close(self) -> None:
        self.sock.close()
