"""The dashboard's own actions — the names it writes into the command file.

Only the handful the control bar still carries.  Every other command the bar
used to write is now posted by the player it belongs to, straight off that
player's own HUD, as a literal string in the player's own repo; the dispatch
loop matches on the string either way.
"""
from __future__ import annotations


QUIT_BUTTON = "quit"
OMNIMINIMIZE = "omniminimize"
OMNIRESTORE = "omnirestore"
OMNIPAUSE_TOGGLE = "omnipause_toggle"
VOICE_TOGGLE = "voice_toggle"
HELP_REFERENCE = "help_reference"
HELP_REFERENCE_CLOSE = "help_reference_close"
# The pair that drives only the dashboard's own reference popup: they open and
# dismiss a help window and reach no player, no shared state.  That is why the
# dispatch loop echoes them as a press and stops there.  It is NOT why omnipause
# would let them through — under a pause they are frozen with everything else
# (see SUSPEND_EXEMPT_COMMANDS in voice_control).
HELP_REFERENCE_COMMANDS = frozenset({HELP_REFERENCE, HELP_REFERENCE_CLOSE})
