from __future__ import annotations

import logging
from collections.abc import Sequence

import xr

logger = logging.getLogger(__name__)


def views_are_renderable(view_state_flags: int) -> bool:
    return bool(view_state_flags & xr.ViewStateFlags.ORIENTATION_VALID_BIT)


def views_are_tracked(view_state_flags: int) -> bool:
    return bool(view_state_flags & xr.ViewStateFlags.ORIENTATION_TRACKED_BIT)


class TrackedViews:
    def __init__(self) -> None:
        self.tracked = False
        self._located = True

    def to_draw(self, view_state_flags: int, views: Sequence) -> list:
        drawn = list(views) if views_are_renderable(view_state_flags) else []
        self.tracked = bool(drawn) and views_are_tracked(view_state_flags)
        self._note(located=bool(drawn))
        return drawn

    def _note(self, *, located: bool) -> None:
        if located == self._located:
            return
        self._located = located
        if located:
            logger.info("Views located again; drawing to the headset once more")
        else:
            logger.warning(
                "The runtime cannot locate the views — submitting no layers, so the "
                "headset is showing its own environment until tracking returns"
            )


def layers_to_submit(views: Sequence, *, project: bool, projection, quads: Sequence) -> list:
    if not views:
        return []
    return [projection, *quads] if project else list(quads)
