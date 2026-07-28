from __future__ import annotations

from dataclasses import dataclass


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
                drop_from_playlist=False,
                move_to_weird=False,
                open_rfb_tab=True,
                log_message=f"Locked {player_name} satellite",
            )
        return LockActionPlan(
            next_locked=False,
            ensure_in_favs=False,
            remove_from_favs=False,
            advance_playlist=True,
            drop_from_playlist=False,
            move_to_weird=False,
            open_rfb_tab=False,
            log_message=f"Unlocked {player_name} satellite",
        )

    if action == "discard":
        # Discard is a demotion by one step, not a single verdict.  On a
        # favorite it only undoes the favoriting — locking is what put the clip
        # in the favs list, so the same gesture has to be able to take it back
        # out.  Nothing else about the clip changes: the file stays in the
        # library and the clip stays in the playlist, so moving on from it is a
        # plain advance and stepping back returns to it.  Only a clip that is
        # *not* a favorite is condemned — dropped from the playlist and moved to
        # the weird dir — which is what a second press on a demoted clip does.
        if is_favorite and current_path:
            return LockActionPlan(
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
        return LockActionPlan(
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

    raise ValueError(f"Unsupported lock action: {action}")
