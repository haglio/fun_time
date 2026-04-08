from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LockActionPlan:
    next_locked: bool
    repeat_mode: str
    ensure_in_favs: bool
    remove_from_favs: bool
    advance_playlist: bool
    move_to_weird: bool
    open_rfb_tab: bool
    log_message: str


def build_lock_plan(action: str, *, which: int, locked: bool, current_path: str) -> LockActionPlan:
    player_name = "portrait" if which == 2 else "landscape"

    if action == "toggle-lock":
        if not locked:
            return LockActionPlan(
                next_locked=True,
                repeat_mode="one",
                ensure_in_favs=bool(current_path),
                remove_from_favs=False,
                advance_playlist=False,
                move_to_weird=False,
                open_rfb_tab=True,
                log_message=f"Locked {player_name} VLC",
            )
        return LockActionPlan(
            next_locked=False,
            repeat_mode="all",
            ensure_in_favs=False,
            remove_from_favs=False,
            advance_playlist=True,
            move_to_weird=False,
            open_rfb_tab=False,
            log_message=f"Unlocked {player_name} VLC",
        )

    if action == "cancel-lock":
        return LockActionPlan(
            next_locked=False,
            repeat_mode="all" if locked else "",
            ensure_in_favs=False,
            remove_from_favs=False,
            advance_playlist=False,
            move_to_weird=False,
            open_rfb_tab=False,
            log_message="",
        )

    if action == "discard":
        return LockActionPlan(
            next_locked=False,
            repeat_mode="all" if locked else "",
            ensure_in_favs=False,
            remove_from_favs=bool(current_path),
            advance_playlist=True,
            move_to_weird=bool(current_path),
            open_rfb_tab=False,
            log_message=f"Discarding from player {which}: {current_path}",
        )

    raise ValueError(f"Unsupported lock action: {action}")
