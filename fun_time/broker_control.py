"""fun_time's side of the OSR2 broker's command channel.

That file holds exactly one verb, unlike a satellite's queue: the broker reads
it whole, strips it and blanks it, so a second verb written before the next
tick replaces the first.  Writes here overwrite to match.  Nothing clears it
when the broker starts, so a verb written while none is up survives.
"""
from __future__ import annotations

from pathlib import Path

from player_core.console import OSR2_PARKED, OSR2_RETRACTED

# Send the OSR2 home and hold it there.  The broker fires the park a second
# later (``L00000I500``: position 0 over half a second) and mutes the script
# feed meanwhile, so an in-flight tail cannot immediately undo it.
PARK_CMD = "PARK"
# Park's antonym: send the OSR2 to the far end of its travel instead of home,
# which is how a relief omnipause gets the device off the user.  Fires the same
# way, under the same mute — only the position it lands on differs.
RETRACT_CMD = "RETRACT"
# Hand the device back to the script feed: cancels a park or retract that has
# not fired yet and lifts the mute.
RESUME_CMD = "RESUME"

HOLD_VERB = {OSR2_PARKED: PARK_CMD, OSR2_RETRACTED: RETRACT_CMD}


def write_broker_command(cmd_file: str | Path, verb: str) -> None:
    """Give the broker *verb* to consume on its next tick."""
    target = Path(cmd_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(verb, encoding="utf-8")
