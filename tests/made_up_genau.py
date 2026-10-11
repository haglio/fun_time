"""Genau's folder, made up for a test, laid out the way every app finds it."""
from __future__ import annotations

from pathlib import Path

from app_support.flick_folder import audio_in, flat_flicks_in


def made_up_genau(root: Path) -> Path:
    genau = root / "genau"
    flat_flicks_in(genau / "flicks").mkdir(parents=True, exist_ok=True)
    audio_in(genau).mkdir(parents=True, exist_ok=True)
    return genau
