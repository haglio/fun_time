from __future__ import annotations

import socket
from pathlib import Path


class RobotHandNotifier:
    def __init__(self, host: str, port: int, *, sock=None):
        self.host = host
        self.port = port
        self.sock = sock if sock is not None else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.last_visible_sent: int | None = None

    def _send(self, message: str) -> None:
        self.sock.sendto(message.encode("utf-8"), (self.host, self.port))

    def notify_clip(self, path: Path) -> None:
        self._send(f"CLIP {path.stem}")

    def notify_visible(self, is_visible: bool) -> None:
        value = 1 if is_visible else 0
        if self.last_visible_sent == value:
            return
        self._send(f"VISIBLE {value}")
        self.last_visible_sent = value

    def sync_window_visibility(
        self,
        *,
        desired_visible: bool,
        window_visible: bool,
        current_clip_path: Path | None,
        show_window,
        hide_window,
    ) -> bool:
        if desired_visible == window_visible:
            return window_visible

        if desired_visible:
            if self.last_visible_sent != 1 and current_clip_path is not None:
                self.notify_clip(current_clip_path)
            self.notify_visible(True)
            show_window()
        else:
            self.notify_visible(False)
            hide_window()
        return desired_visible

    def close(self) -> None:
        self.sock.close()
