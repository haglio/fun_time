from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LockActionPlan:
    next_locked: bool
    ensure_in_favs: bool
    remove_from_favs: bool
    advance_playlist: bool
    move_to_weird: bool
    open_rfb_tab: bool
    log_message: str


def build_lock_plan(
    action: str,
    *,
    which: int,
    locked: bool,
    current_path: str,
    is_favorite: bool = False,
) -> LockActionPlan:
    player_name = "portrait" if which == 2 else "landscape"

    if action == "toggle-lock":
        if not locked:
            return LockActionPlan(
                next_locked=True,
                ensure_in_favs=bool(current_path),
                remove_from_favs=False,
                advance_playlist=False,
                move_to_weird=False,
                open_rfb_tab=True,
                log_message=f"Locked {player_name} satellite",
            )
        return LockActionPlan(
            next_locked=False,
            ensure_in_favs=False,
            remove_from_favs=False,
            advance_playlist=True,
            move_to_weird=False,
            open_rfb_tab=False,
            log_message=f"Unlocked {player_name} satellite",
        )

    if action == "discard":
        # Discard is a demotion by one step, not a single verdict.  On a
        # favorite it only undoes the favoriting: the row leaves the favs list
        # and the clip leaves the playlist, but the file stays in the library —
        # locking is what put it there, so the same gesture has to be able to
        # take it back out without condemning it.  Only a clip that is *not* a
        # favorite is marked weird, so meeting a demoted clip again and pressing
        # again is what finally moves the file out.
        if is_favorite and current_path:
            return LockActionPlan(
                next_locked=False,
                ensure_in_favs=False,
                remove_from_favs=True,
                advance_playlist=True,
                move_to_weird=False,
                open_rfb_tab=False,
                log_message=f"Removed from favorites on player {which}: {current_path}",
            )
        return LockActionPlan(
            next_locked=False,
            ensure_in_favs=False,
            remove_from_favs=bool(current_path),
            advance_playlist=True,
            move_to_weird=bool(current_path),
            open_rfb_tab=False,
            log_message=f"Discarding from player {which}: {current_path}",
        )

    raise ValueError(f"Unsupported lock action: {action}")
