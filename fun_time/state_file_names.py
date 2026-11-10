from __future__ import annotations

from pathlib import Path

RETIRED_PREFIX = "nau"
PREFIX = "main_player"


def _a_later_session_already_wrote(new: Path) -> bool:
    return new.exists()


def take_up_the_retired_state_file_names(state_dir: Path) -> list[Path]:
    taken_up: list[Path] = []
    for old in sorted(Path(state_dir).glob(f"{RETIRED_PREFIX}*")):
        if not old.is_file():
            continue
        new = old.with_name(PREFIX + old.name[len(RETIRED_PREFIX):])
        if _a_later_session_already_wrote(new):
            continue
        old.rename(new)
        taken_up.append(new)
    return taken_up
