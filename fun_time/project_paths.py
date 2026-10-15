"""Where this checkout is, and the files that sit at its root.

A leaf on purpose: nothing here imports anything but ``pathlib``, so a module
that wants a path does not take the config reader and the loopback server with
it to get one.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ICON = PROJECT_DIR / "icon.ico"
PROJECT_VR_ICON = PROJECT_DIR / "vr_icon.ico"
