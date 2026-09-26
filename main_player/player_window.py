from __future__ import annotations

import warnings


def take_outside_resizes(pygame) -> None:
    # SDL pins a borderless window that is not resizable to its own idea of its
    # size in WM_NCCALCSIZE, so a resize from Fun Time moved the window without
    # growing or shrinking what it draws, and mpv drew on at the old size.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pygame.Window.from_display_module().resizable = True
