"""The lines Kino adds to the Main Funestra's status file for the Fun Time orchestrator.

After the lines every Funestra publishes come Kino's own -- its loop, and the
video's place in the library -- which FunTimeVR's main role keeps in step.
The dispatch side reads them to light the console's buttons and to hand a loop
back to a reopened session.
"""
from __future__ import annotations

from dataclasses import dataclass

from player_core.modes import LengthMode


@dataclass(frozen=True)
class LibraryStatus:
    length_mode: LengthMode | None = None
    compilation: str = ""
    has_compilation: bool = False
    has_other_versions: bool = False
    jump_to: str = ""


def status_fields(loops, library: LibraryStatus | None = None) -> dict[str, str]:
    library = library or LibraryStatus()
    loop_in_ms, loop_out_ms = loops.bounds or (0, 0)
    return {
        "loop_state": str(loops.state),
        "loop_in_ms": str(int(loop_in_ms)),
        "loop_out_ms": str(int(loop_out_ms)),
        "length_mode": library.length_mode or "",
        "compilation": library.compilation,
        "has_compilation": "1" if library.has_compilation else "0",
        "has_other_versions": "1" if library.has_other_versions else "0",
        "jump_to": library.jump_to,
    }
