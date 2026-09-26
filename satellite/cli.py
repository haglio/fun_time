"""Argument parsing and playlist resolution for a satellite player — no pygame.

Kept apart from the run loop so what fun_time hands each satellite
(:mod:`satellite.contract`) is importable and testable without an SDL display,
exactly as the main player's own CLI is.  A satellite is always given an
explicit ``--playlist``, so there is no library discovery here.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from player_core.playlist import PlaylistItem, read_playlist

from fun_time.win32_desktop import on_hidden_desktop


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A native satellite video player")
    p.add_argument("--playlist", type=Path, default=None,
                   help="Video or picture list to play, one per line; a funscript "
                        "column after a TAB is ignored")
    p.add_argument("--command-file", type=Path, default=None,
                   help="Poll this file for orchestrator commands")
    p.add_argument("--paused-file", type=Path, default=None,
                   help="Flag file that owns the paused state when present")
    p.add_argument("--status-file", type=Path, default=None,
                   help="Publish playback status to this file")
    p.add_argument("--play-points-file", type=Path, default=None,
                   help="Where this player writes down the point each clip was "
                        "left at, so playing one again picks up there")
    p.add_argument("--hud-file", type=Path, default=None,
                   help="Lock-HUD panel fun_time publishes; drawn into the video")
    p.add_argument("--dashboard-cmd-file", type=Path, default=None,
                   help="Where a click on the lock HUD, or on the picture itself, "
                        "posts its fun_time command")
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--x", type=int, default=None)
    p.add_argument("--y", type=int, default=None)
    p.add_argument("--title", type=str, default="Satellite",
                   help="Window caption; fun_time gives each satellite a distinct one "
                        "so it can resolve each window to its portrait/landscape slot")
    p.add_argument("--no-audio", action="store_true", default=False,
                   help="Never play audio, and leave the volume chip a read-only "
                        "indicator; without it a satellite still opens muted, but "
                        "its chip can unmute it")
    p.add_argument("--tile", action="store_true", default=False,
                   help="Show a portrait picture side by side, as many times as "
                        "fit, while the window is wider than it is tall")
    p.add_argument("--taskbar-identity", default=None,
                   help="Group this window under the launching application's taskbar "
                        "button; the orchestrator passes its own AppUserModelID. "
                        "Without one the window falls under whatever the interpreter's "
                        "path is registered to, which is some other program entirely")
    return p


def resolve_playlist(args) -> list[PlaylistItem]:
    """The videos to play, from the explicit ``--playlist`` file.  No file means
    nothing to play (fun_time always supplies one; standalone without it is an
    error the caller reports)."""
    if args.playlist is None:
        return []
    return read_playlist(Path(args.playlist))


def audio_muted(args) -> bool:
    """Silent for good: by ``--no-audio``, the ``FUN_TIME_MUTE_AUDIO`` contract, or
    being off-screen -- so a hidden-desktop run is inaudible however it was launched."""
    return (bool(args.no_audio)
            or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"
            or on_hidden_desktop())
