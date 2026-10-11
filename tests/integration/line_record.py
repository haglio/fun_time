from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app_support.file_channel import write_whole

KEPT_FOR = timedelta(days=14)


def _now() -> datetime:
    return datetime.now().astimezone()


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


@dataclass
class Visit:
    path: Path
    record: dict = field(default_factory=dict)

    @classmethod
    def join(cls, runs: Path, *, checkout: str, branch: str, what: list[str], short: bool,
             now: datetime | None = None) -> Visit:
        joined = now or _now()
        runs.mkdir(parents=True, exist_ok=True)
        _forget_runs_before(runs, joined - KEPT_FOR)
        visit = cls(runs / f"{joined:%Y%m%d-%H%M%S}-{os.getpid()}.json",
                    {"checkout": checkout, "branch": branch, "what": what, "short": short, "pid": os.getpid(),
                     "joined": _stamp(joined)})
        visit._save()
        return visit

    def got_the_machine(self, now: datetime | None = None) -> None:
        self.record["got_the_machine"] = _stamp(now or _now())
        self._save()

    def ended_with(self, exit_code: int, now: datetime | None = None) -> int:
        self.record.update(ended=_stamp(now or _now()), exit_code=exit_code)
        self._save()
        return exit_code

    def _save(self) -> None:
        write_whole(self.path, json.dumps(self.record, indent=1))


def _forget_runs_before(runs: Path, cutoff: datetime) -> None:
    for record in runs.glob("*.json"):
        with contextlib.suppress(OSError):
            if record.stat().st_mtime < cutoff.timestamp():
                record.unlink()
