"""The verbs that move a main player's A/B loop, spelled once for its three
readers: each main player's registry, and the orchestrator that hands a loop back
to a reopened session.  ``SET_LOOP`` carries a finished range, ``<in_ms> <out_ms>``.
"""
from __future__ import annotations

__all__ = [
    "LOOP_CANCEL",
    "RECORD_DOWN",
    "RECORD_TAP",
    "RECORD_UP",
    "SET_LOOP",
]

RECORD_DOWN = "RECORD_DOWN"
RECORD_UP = "RECORD_UP"
RECORD_TAP = "RECORD_TAP"
LOOP_CANCEL = "LOOP_CANCEL"
SET_LOOP = "SET_LOOP"
