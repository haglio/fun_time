from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

from .win32_process import get_process_creation_time

OFFER_NAME = "fun_time_offer.txt"
OFFER_STILL_STARTING = "starting"
TAKEOVER_NAME = "fun_time_takeover.json"
SESSION_NAME = "fun_time_session.txt"
RELEASE = "RELEASE"


class OpenOrigenerator(NamedTuple):
    pid: int
    starting: bool


def _state_dir(origenerator_dir: str | Path) -> Path:
    return Path(origenerator_dir) / "state"


def the_open_origenerator(origenerator_dir: str | Path) -> OpenOrigenerator | None:
    try:
        pid, created_at, *still = (_state_dir(origenerator_dir) / OFFER_NAME).read_text(
            encoding="utf-8").split()
        pid, created_at = int(pid), int(created_at)
    except (OSError, ValueError):
        return None
    if still not in ([], [OFFER_STILL_STARTING]):
        return None
    if get_process_creation_time(pid) != created_at:
        return None
    return OpenOrigenerator(pid, starting=bool(still))


def take_it_over(origenerator_dir: str | Path, *, pid: int, args: list[str]) -> None:
    takeover = _state_dir(origenerator_dir) / TAKEOVER_NAME
    staged = takeover.with_name(f"{TAKEOVER_NAME}.tmp")
    staged.write_text(json.dumps({"pid": pid, "args": args}), encoding="utf-8")
    os.replace(staged, takeover)


def claim_the_osr2(origenerator_dir: str | Path) -> None:
    created_at = get_process_creation_time(os.getpid())
    if created_at is None:
        return
    state = _state_dir(origenerator_dir)
    state.mkdir(parents=True, exist_ok=True)
    (state / SESSION_NAME).write_text(f"{os.getpid()} {created_at}", encoding="utf-8")
