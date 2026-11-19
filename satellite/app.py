from __future__ import annotations

from funestra.program.app import main as run_the_funestra_program

from .contract import SATELLITE_CHANNELS
from .silence import silenced


def main(argv: list[str] | None = None) -> int:
    return run_the_funestra_program(argv, channels=SATELLITE_CHANNELS, silent=silenced())
