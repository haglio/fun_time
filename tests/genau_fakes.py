from __future__ import annotations

from pathlib import Path

from funestra.genau_refresh import SEND_THREAD

from fun_time.genau_engine import UDP_READER_THREAD

RUN_FOR_GOOD = (UDP_READER_THREAD, SEND_THREAD)


def every_thread_now_but_the_ones_that_run_for_good(*, target, args=(), name=""):
    if name not in RUN_FOR_GOOD:
        target(*args)


class ThreadsAsked:
    def __init__(self) -> None:
        self.named: list[str] = []

    def __call__(self, *, target, args=(), name=""):
        self.named.append(name)
        every_thread_now_but_the_ones_that_run_for_good(target=target, args=args, name=name)


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
