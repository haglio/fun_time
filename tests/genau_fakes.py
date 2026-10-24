from __future__ import annotations

from pathlib import Path


class FakeSink:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    def want(self, wanted: bool) -> None:
        pass

    def send(self, command: str) -> None:
        self.sent.append(command)

    def close(self) -> None:
        self.closed = True


class FakeNotifier:
    def __init__(self) -> None:
        self.flicks: list[Path] = []
        self.visible: list[bool] = []
        self.closed = False

    def notify_flick(self, path: Path) -> None:
        self.flicks.append(path)

    def notify_visible(self, is_visible: bool) -> None:
        self.visible.append(is_visible)

    def close(self) -> None:
        self.closed = True
