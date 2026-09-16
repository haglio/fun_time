"""What mpv does with a seek it cannot take yet, for the fake players to do too."""
from __future__ import annotations


class RefusesSeeks:
    """Refuse the next seeks the way mpv does before the file it is opening
    plays: python-mpv raises mpv's command error as a SystemError."""

    _refusals_left = 0
    refused = 0

    def refuse_seeks(self, count: int) -> None:
        self._refusals_left = count

    def refuse_if_asked(self) -> None:
        if self._refusals_left > 0:
            self._refusals_left -= 1
            self.refused += 1
            raise SystemError("Error running mpv command", -12)
