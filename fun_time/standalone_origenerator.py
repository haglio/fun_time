from __future__ import annotations

import json
import os
import re
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
OFFER = re.compile(rf"(\d+) (\d+)(?: ({OFFER_STILL_STARTING}))?")
SLIDESHOW_MARK = re.compile(r"(\d+) (\d+)(?: (portrait|landscape))?")


class OpenOrigenerator(NamedTuple):
    pid: int
    starting: bool
    checkout: Path
    slideshow_on: tuple[RoomFunestra, ...]


def _state_dir(origenerator_dir: str | Path) -> Path:
    return Path(origenerator_dir) / "state"


def _read(path: Path, pattern: re.Pattern) -> re.Match | None:
    try:
        return pattern.fullmatch(path.read_text(encoding="utf-8").strip())
    except OSError:
        return None


def the_open_origenerator(origenerator_dir: str | Path) -> OpenOrigenerator | None:
    checkout = Path(origenerator_dir)
    looked_in = dict.fromkeys((checkout, primary_of(checkout)))
    return next(filter(None, map(_offered_in, looked_in)), None)


def _offered_in(checkout: Path) -> OpenOrigenerator | None:
    offer = _read(_state_dir(checkout) / OFFER_NAME, OFFER)
    if offer is None:
        return None
    pid, created_at = int(offer[1]), int(offer[2])
    if get_process_creation_time(pid) != created_at:
        return None
    return OpenOrigenerator(pid, starting=offer[3] is not None, checkout=checkout,
                            slideshow_on=_satellites_its_slideshow_is_on(
                                checkout, pid, created_at))


def _satellites_its_slideshow_is_on(checkout: Path, pid: int,
                                    created_at: int) -> tuple[RoomFunestra, ...]:
    mark = _read(_state_dir(checkout) / SHOWING_NAME, SLIDESHOW_MARK)
    if mark is None or (int(mark[1]), int(mark[2])) != (pid, created_at):
        return ()
    if mark[3] is None:
        return RoomFunestra.SATELLITES
    return (RoomFunestra.PORTRAIT,) if mark[3] == "portrait" else (RoomFunestra.LANDSCAPE,)


def the_takeover_it_was_handed(origenerator_dir: str | Path) -> Path:
    return _state_dir(origenerator_dir) / TAKEOVER_NAME


def takeover_of(pid: int, args: list[str]) -> str:
    return json.dumps({"pid": pid, "args": args})


def take_it_over(origenerator_dir: str | Path, *, pid: int, args: list[str]) -> None:
    takeover = the_takeover_it_was_handed(origenerator_dir)
    staged = takeover.with_name(f"{TAKEOVER_NAME}.tmp")
    staged.write_text(takeover_of(pid, args), encoding="utf-8")
    os.replace(staged, takeover)


def claim_of(pid: int, created_at: int) -> str:
    return f"{pid} {created_at}"


def claim_the_osr2(origenerator_dir: str | Path) -> None:
    created_at = get_process_creation_time(os.getpid())
    if created_at is None:
        return
    checkout = Path(origenerator_dir)
    for claimed in dict.fromkeys((checkout, primary_of(checkout))):
        state = _state_dir(claimed)
        state.mkdir(parents=True, exist_ok=True)
        (state / SESSION_NAME).write_text(claim_of(os.getpid(), created_at), encoding="utf-8")
