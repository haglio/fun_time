from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

from .checkout_overrides import primary_of
from .room_funestras import RoomFunestra
from .win32_process import get_process_creation_time

OFFER_NAME = "fun_time_offer.txt"
OFFER_STILL_STARTING = "starting"
SHOWING_NAME = "fun_time_showing.txt"
TAKEOVER_NAME = "fun_time_takeover.json"
SESSION_NAME = "fun_time_session.txt"
RELEASE = "RELEASE"


class OpenOrigenerator(NamedTuple):
    pid: int
    starting: bool
    checkout: Path
    slideshow_on: tuple[RoomFunestra, ...]


def _state_dir(origenerator_dir: str | Path) -> Path:
    return Path(origenerator_dir) / "state"


def the_open_origenerator(origenerator_dir: str | Path) -> OpenOrigenerator | None:
    checkout = Path(origenerator_dir)
    looked_in = dict.fromkeys((checkout, primary_of(checkout)))
    return next(filter(None, map(_offered_in, looked_in)), None)


def _offered_in(checkout: Path) -> OpenOrigenerator | None:
    try:
        pid, created_at, *still = (_state_dir(checkout) / OFFER_NAME).read_text(
            encoding="utf-8").split()
        pid, created_at = int(pid), int(created_at)
    except (OSError, ValueError):
        return None
    if still not in ([], [OFFER_STILL_STARTING]):
        return None
    if get_process_creation_time(pid) != created_at:
        return None
    return OpenOrigenerator(pid, starting=bool(still), checkout=checkout,
                            slideshow_on=_satellites_its_slideshow_is_on(
                                checkout, pid, created_at))


def _satellites_its_slideshow_is_on(checkout: Path, pid: int,
                                    created_at: int) -> tuple[RoomFunestra, ...]:
    try:
        mark = (_state_dir(checkout) / SHOWING_NAME).read_text(encoding="utf-8").split()
    except OSError:
        return ()
    if mark[:2] != [str(pid), str(created_at)]:
        return ()
    monitor = mark[2:]
    if not monitor:
        return RoomFunestra.SATELLITES
    return tuple(funestra for funestra in RoomFunestra.SATELLITES if [funestra.label] == monitor)


def the_takeover_it_was_handed(origenerator_dir: str | Path) -> Path:
    return _state_dir(origenerator_dir) / TAKEOVER_NAME


def take_it_over(origenerator_dir: str | Path, *, pid: int, args: list[str]) -> None:
    takeover = the_takeover_it_was_handed(origenerator_dir)
    staged = takeover.with_name(f"{TAKEOVER_NAME}.tmp")
    staged.write_text(json.dumps({"pid": pid, "args": args}), encoding="utf-8")
    os.replace(staged, takeover)


def claim_the_osr2(origenerator_dir: str | Path) -> None:
    created_at = get_process_creation_time(os.getpid())
    if created_at is None:
        return
    checkout = Path(origenerator_dir)
    for claimed in dict.fromkeys((checkout, primary_of(checkout))):
        state = _state_dir(claimed)
        state.mkdir(parents=True, exist_ok=True)
        (state / SESSION_NAME).write_text(f"{os.getpid()} {created_at}", encoding="utf-8")
