"""The tones and face the loading and closing covers are painted in.

Their own, not shared_ui's: nothing there matches these five, and a cover is a
tkinter subprocess whose whole job is to be on screen fast — `shared_ui.colors`
and `shared_ui.fonts` both import PyQt6, which the covers never load.  Adding
the real tokens to shared_ui is that repo's change to make.

Plain strings, because the wordmark's pink is the panel's too, and neither
module can import the other without taking a whole toolkit with it.
"""
from __future__ import annotations

BG = "#1a1a2e"
WORDMARK_PINK = "#e94560"
TROUGH = "#16213e"
TEXT_DIM = "#c0c0d8"
HINT_DIM = "#7a7a95"  # subtler than the status line, still legible on BG
FACE = "Segoe UI"     # the same face shared_ui.fonts names, at no Qt cost here
