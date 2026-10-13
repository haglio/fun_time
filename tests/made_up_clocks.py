from __future__ import annotations


class SleepDrivenClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    monotonic = seconds = time

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class StarvedRun:
    def seconds(self) -> float:
        return 0.0
