"""park / retract / release: the arithmetic of the spoken holds on Genau's
stroke.  Why each ordering is what it is, is pinned in test_genau_hold.py."""
from __future__ import annotations

from dataclasses import dataclass

# The two ends the broker's own PARK and RETRACT hold, hence the two words.
HOLD_CENTERS: dict[str, int] = {"genau_park": 0, "genau_retract": 100}


@dataclass(frozen=True)
class StrokeDials:
    """Genau's stroke as it stood, and as it can be asked for again."""
    cruise: bool
    speed: int
    amplitude: int
    center: int


def hold_commands(center: int) -> tuple[str, ...]:
    """Still the stroke at *center*; cruise off first, as it rewrites the dials."""
    return ("CRUISE_OFF", "AMP 0", f"CENTER {center}", "SPEED 0")


def release_commands(dials: StrokeDials) -> tuple[str, ...]:
    """Put *dials* back; cruise last, as it draws its waves from what they say."""
    return (
        f"AMP {dials.amplitude}",
        f"CENTER {dials.center}",
        f"SPEED {dials.speed}",
        "CRUISE_ON" if dials.cruise else "CRUISE_OFF",
    )


def dials_text(dials: StrokeDials) -> str:  # the shape Genau's own files use
    return (
        f"cruise={'1' if dials.cruise else '0'}\n"
        f"speed={dials.speed}\n"
        f"amplitude={dials.amplitude}\n"
        f"center={dials.center}\n"
    )


def parse_dials(text: str) -> StrokeDials | None:  # None as an absent file does
    values = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line
    )
    try:
        return StrokeDials(
            cruise=values["cruise"].strip() == "1",
            speed=int(values["speed"]),
            amplitude=int(values["amplitude"]),
            center=int(values["center"]),
        )
    except (KeyError, ValueError):
        return None
