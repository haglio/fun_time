from __future__ import annotations

from dataclasses import dataclass

from .players import Player


@dataclass(frozen=True)
class LockActionPlan:
    next_locked: bool
    ensure_in_favs: bool
    remove_from_favs: bool
    advance_playlist: bool
    # Whether advancing also takes the clip out of the playlist it is advancing
    # from.  Only a condemned clip is dropped; everything else just moves on and
    # stays where it was, so stepping back lands on it again.
    drop_from_playlist: bool
    move_to_weird: bool
    open_rfb_tab: bool
    log_message: str
    # The glanceable version of ``log_message``, flashed over the player the
    # action was about.  Empty when there is nothing to announce — the action
    # was a no-op, or the log line is all it warrants.
    notice_message: str = ""
    # Whether that notice is about the favorites, which is what decides its
    # color: the caller flashes those green and everything else white.  Said as
    # a fact about the action rather than as a log level, so this module stays
    # free of logging — the two things a discard can be look identical otherwise.
    notice_about_favorites: bool = False

    @classmethod
    def lock(cls, player_name: str, *, current_path: str) -> LockActionPlan:
        return cls(
            next_locked=True,
            ensure_in_favs=bool(current_path),
            remove_from_favs=False,
            advance_playlist=False,
            drop_from_playlist=False,
            move_to_weird=False,
            open_rfb_tab=True,
            log_message=f"Locked {player_name} satellite",
        )

    @classmethod
    def unlock(cls, player_name: str) -> LockActionPlan:
        return cls(
            next_locked=False,
            ensure_in_favs=False,
            remove_from_favs=False,
            advance_playlist=True,
            drop_from_playlist=False,
            move_to_weird=False,
            open_rfb_tab=False,
            log_message=f"Unlocked {player_name} satellite",
        )

    @classmethod
    def demote(cls, which: int, current_path: str) -> LockActionPlan:
        return cls(
            next_locked=False,
            ensure_in_favs=False,
            remove_from_favs=True,
            advance_playlist=True,
            drop_from_playlist=False,
            move_to_weird=False,
            open_rfb_tab=False,
            log_message=f"Removed from favorites on player {which}: {current_path}",
            notice_message="Unfavorited",
            notice_about_favorites=True,
        )

    @classmethod
    def condemn(cls, which: int, current_path: str) -> LockActionPlan:
        return cls(
            next_locked=False,
            ensure_in_favs=False,
            remove_from_favs=bool(current_path),
            advance_playlist=True,
            drop_from_playlist=True,
            move_to_weird=bool(current_path),
            open_rfb_tab=False,
            log_message=f"Discarding from player {which}: {current_path}",
            # Only when there is a clip to condemn: with no current path the
            # discard touches nothing, and announcing it would be a lie.
            notice_message="Marked weird" if current_path else "",
        )


def build_lock_toggle_plan(*, which: int, locked: bool, current_path: str) -> LockActionPlan:
    player_name = Player(which).label
    if locked:
        return LockActionPlan.unlock(player_name)
    return LockActionPlan.lock(player_name, current_path=current_path)


def build_discard_plan(
    *, which: int, current_path: str, is_favorite: bool = False
) -> LockActionPlan:
    """Two steps, not one verdict: locking is what put a clip in the favs list,
    so a first discard only takes it back out, and a second — the clip no longer
    a favorite — condemns it.
    """
    if is_favorite and current_path:
        return LockActionPlan.demote(which, current_path)
    return LockActionPlan.condemn(which, current_path)
