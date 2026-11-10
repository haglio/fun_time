from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from app_support.file_channel import read_key_values
from funestra.modes import LoopState, read_mode
from funestra.status import FunestraStatus, parse_status


@dataclass(frozen=True)
class ScriptedStatus(FunestraStatus):
    has_funscript: bool = False
    funscript_resting: bool = False
    handoff_touch_ms: int | None = None

    @property
    def funscript_driving(self) -> bool:
        return self.has_funscript and not self.funscript_resting


def scripted_fields(values: dict[str, str]) -> dict:
    return {
        "has_funscript": _status_bool(values, "has_funscript"),
        "funscript_resting": _status_bool(values, "funscript_resting"),
        "handoff_touch_ms": _status_touch(values),
    }


MAIN_FUNESTRA_OPENS_LOCKED = True
GENAU_OPENS_LOCKED = True


@dataclass(frozen=True)
class MainFunestraStatus(ScriptedStatus):
    locked: bool = MAIN_FUNESTRA_OPENS_LOCKED
    loop_state: LoopState = LoopState.NORMAL
    loop_in_ms: int = 0
    loop_out_ms: int = 0
    compilation: str = ""
    has_compilation: bool = False
    has_other_versions: bool = False
    jump_to: str = ""
    portrait: bool | None = None

    @property
    def loop_bounds(self) -> tuple[int, int] | None:
        if self.loop_state is not LoopState.LOOPING or self._its_range_would_hold_mpv_on_one_frame:
            return None
        return (self.loop_in_ms, self.loop_out_ms)

    @property
    def _its_range_would_hold_mpv_on_one_frame(self) -> bool:
        return self.loop_out_ms <= self.loop_in_ms


__all__ = ["read_key_values"]

_A_MAIN_FUNESTRA_WITH_NOTHING_TO_SAY = FunestraStatus(locked=MAIN_FUNESTRA_OPENS_LOCKED)


def read_main_funestra_status(path: Path, *, fallback: MainFunestraStatus | None = None) -> MainFunestraStatus:
    if not path.exists():
        return fallback or MainFunestraStatus()
    try:
        return _main_funestra_status_from(read_key_values(path))
    except (OSError, ValueError):
        return fallback or MainFunestraStatus()


def _main_funestra_status_from(values: dict) -> MainFunestraStatus:
    return MainFunestraStatus(
        **asdict(parse_status(values, default=_A_MAIN_FUNESTRA_WITH_NOTHING_TO_SAY)),
        loop_state=read_mode(LoopState, values.get("loop_state", "").strip(), LoopState.NORMAL),
        **scripted_fields(values),
        loop_in_ms=int(values.get("loop_in_ms", "0").strip() or 0),
        loop_out_ms=int(values.get("loop_out_ms", "0").strip() or 0),
        compilation=values.get("compilation", "").strip(),
        has_compilation=_status_bool(values, "has_compilation"),
        has_other_versions=_status_bool(values, "has_other_versions"),
        jump_to=values.get("jump_to", "").strip(),
        portrait=_status_flag_or_none(values, "portrait"),
    )


def published_main_funestra_status(path: Path) -> MainFunestraStatus | None:
    try:
        values = read_key_values(path)
        return _main_funestra_status_from(values) if "speed" in values else None
    except (OSError, ValueError):
        return None


def _status_flag_or_none(values: dict, key: str) -> bool | None:
    return {"1": True, "0": False}.get(values.get(key, "").strip())


def _status_touch(values: dict) -> int | None:
    raw = values.get("handoff_touch_ms", "").strip()
    return int(raw) if raw.isdigit() else None


@dataclass(frozen=True)
class GenauStatus:
    learned_active: bool = False
    locked: bool = GENAU_OPENS_LOCKED
    shape: str = "sine"
    flick: str = ""
    flipped: bool = False
    portrait: bool | None = None


def _status_bool(values: dict[str, str], key: str, *, on_at_rest: bool = False) -> bool:
    return values.get(key, "1" if on_at_rest else "0").strip() not in ("0", "false", "")


def read_genau_status(path: Path) -> GenauStatus:
    if not path.exists():
        return GenauStatus()
    try:
        return _genau_status_from(read_key_values(path))
    except (OSError, ValueError):
        return GenauStatus()


def published_genau_status(path: Path) -> GenauStatus | None:
    try:
        values = read_key_values(path)
        return _genau_status_from(values) if "locked" in values else None
    except (OSError, ValueError):
        return None


def _genau_status_from(values: dict) -> GenauStatus:
    return GenauStatus(
        learned_active=_status_bool(values, "learned"),
        locked=_status_bool(values, "locked", on_at_rest=GENAU_OPENS_LOCKED),
        shape=values.get("shape", "sine").strip(),
        flick=values.get("flick", "").strip(),
        flipped=_status_bool(values, "flipped"),
        portrait=_status_flag_or_none(values, "portrait"),
    )


def origenerator_has_published(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        read_key_values(path)
    except (OSError, ValueError):
        return False
    return True


CORE_READY = "core_ready"


def origenerator_core_waits(path: Path) -> bool:
    try:
        return read_key_values(path).get(CORE_READY, "").strip() == "0"
    except (OSError, ValueError):
        return False
