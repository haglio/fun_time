"""fun_time's read side of the hosted Origenerator's status channel.

The hosted app (see :mod:`fun_time.satellites_mode`) publishes which satellite
regions its shows currently cover, the same ``key=value`` idiom the native
satellites publish theirs in (:mod:`fun_time.satellite_control`).  The dispatch
loop reads it to pause the player a show covers and resume it when the show
closes; the write side lives in origenerator's own ``fun_time_bridge``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OrigeneratorStatus:
    """Which satellite regions the hosted app's shows cover.

    The file also carries each side's ``_video`` and ``_locked`` (see the
    README's runtime-files section) — published for whoever looks, read by
    nobody here yet, so this parses only what the session actually consumes.
    """

    portrait_active: bool = False
    landscape_active: bool = False

    def side_active(self, side: str) -> bool:
        return self.portrait_active if side == "portrait" else self.landscape_active


def read_origenerator_status(status_file: Path) -> OrigeneratorStatus:
    """Parse the hosted app's status file; absent or blank reads as no shows."""
    try:
        text = Path(status_file).read_text(encoding="utf-8")
    except OSError:
        return OrigeneratorStatus()
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return OrigeneratorStatus(
        portrait_active=fields.get("portrait_active") == "1",
        landscape_active=fields.get("landscape_active") == "1",
    )
