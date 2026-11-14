from __future__ import annotations

import argparse
import os
from pathlib import Path

from funestra.channel_manifest import add_channel_arguments
from funestra.playlist import PlaylistItem, read_playlist

from fun_time.win32_desktop import on_hidden_desktop

from .contract import SATELLITE_CHANNELS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A Satellite Funestra of Fun Time")
    add_channel_arguments(p, SATELLITE_CHANNELS)
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--x", type=int, default=None)
    p.add_argument("--y", type=int, default=None)
    p.add_argument("--title", type=str, default="Satellite",
                   help="Window caption; fun_time gives each satellite a distinct one "
                        "so it can resolve each window to its Portrait or Landscape Satellite")
    p.add_argument("--no-audio", action="store_true", default=False,
                   help="Never play audio, and leave the volume control a read-only "
                        "indicator; without it a satellite still opens muted, but "
                        "its chip can unmute it")
    p.add_argument("--tile", action="store_true", default=False,
                   help="Show a portrait item side by side, as many times as "
                        "fit, while the window is wider than it is tall")
    p.add_argument("--taskbar-identity", default=None,
                   help="Group this window under the launching application's taskbar "
                        "button; the orchestrator passes its own AppUserModelID. "
                        "Without one the window falls under whatever the interpreter's "
                        "path is registered to, which is some other program entirely")
    p.add_argument("--icon", type=Path, default=None,
                   help="The window icon Fun Time hands over, so an Alt-Tab entry "
                        "and a taskbar thumbnail show which session it belongs to")
    return p


def resolve_playlist(args) -> list[PlaylistItem]:
    if args.playlist is None:
        return []
    return read_playlist(Path(args.playlist))


def audio_muted(args) -> bool:
    return (bool(args.no_audio)
            or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"
            or on_hidden_desktop())
