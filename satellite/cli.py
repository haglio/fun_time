"""Argument parsing and playlist resolution for a satellite player — no pygame.

Kept apart from the run loop so the orchestrator-facing surface (the file
quartet fun_time hands each satellite, the window geometry) is importable and
testable without an SDL display, exactly as Nau's own CLI is.  A satellite always
receives an explicit ``--playlist`` from fun_time, so there is no library
discovery or version grouping here — just read the list.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from player_core.playlist import read_playlist


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A native satellite video player")
    p.add_argument("--playlist", type=Path, default=None,
                   help="Video (or video<TAB>funscript) list to play; funscripts are ignored")
    p.add_argument("--command-file", type=Path, default=None,
                   help="Poll this file for orchestrator commands")
    p.add_argument("--paused-file", type=Path, default=None,
                   help="Flag file that owns the paused state when present")
    p.add_argument("--status-file", type=Path, default=None,
                   help="Publish playback status to this file")
    p.add_argument("--hud-file", type=Path, default=None,
                   help="Lock-HUD panel fun_time publishes; drawn into the video")
    p.add_argument("--dashboard-cmd-file", type=Path, default=None,
                   help="Where a click on the lock HUD posts its fun_time command")
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--x", type=int, default=None)
    p.add_argument("--y", type=int, default=None)
    p.add_argument("--title", type=str, default="Satellite",
                   help="Window caption; fun_time gives each satellite a distinct one "
                        "so it can resolve each window to its portrait/landscape slot")
    p.add_argument("--no-audio", action="store_true", default=False,
                   help="Never play audio (a satellite is silent)")
    p.add_argument("--taskbar-identity", default=None,
                   help="Group this window under the launching application's taskbar "
                        "button; the orchestrator passes its own AppUserModelID. "
                        "Without one the window falls under whatever the interpreter's "
                        "path is registered to, which is some other program entirely")
    return p


def resolve_playlist(args) -> list[Path]:
    """The videos to play, from the explicit ``--playlist`` file.

    A satellite is silent and unscripted, so the funscript column of Nau's shared
    playlist format is dropped.  No file means nothing to play (fun_time always
    supplies one; standalone without it is an error the caller reports).
    """
    if args.playlist is None:
        return []
    return [video for video, _funscript in read_playlist(Path(args.playlist))]


def audio_muted(args) -> bool:
    """Whether the satellite stays silent — always, in practice.

    Satellites carry no audio, but honor ``--no-audio`` and the
    ``FUN_TIME_MUTE_AUDIO=1`` contract the rest of the stack uses so a run never
    spends ffmpeg on audio it will not play.
    """
    return bool(args.no_audio) or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"
