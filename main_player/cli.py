"""Argument parsing and playlist resolution for the main player — no pygame imports.

Kept apart from the app shell so the orchestrator-facing configuration
surface is importable (and testable) without an SDL display.
"""
from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from pathlib import Path

from app_support import ports
from player_core.playlist import PlaylistItem, read_playlist

from fun_time.win32_desktop import on_hidden_desktop

from .duration_cache import DurationCache
from .library import collapse_playlist_versions
from .library_source import LibrarySource, build_library_source
from .mode_memory import ModeMemory
from .play_points import PlayPoints, play_points_filename


def load_config(config_path: Path) -> dict:
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def build_parser(config: dict) -> argparse.ArgumentParser:
    main_player = config.get("main_player", {})
    p = argparse.ArgumentParser(description="Fun Time's main player: a funscript video player")
    p.add_argument("--config", type=Path, default=None,
                   help="Fun Time's config file, whose main_player section supplies "
                        "the defaults below")
    p.add_argument("--videos-dir", type=Path, default=main_player.get("videos_dir"))
    p.add_argument("--scripts-dir", type=Path, default=main_player.get("scripts_dir"))
    p.add_argument("--clips-dir", type=Path, default=main_player.get("clips_dir") or config.get("clips_dir"),
                   help="Genau's delivery folder: its loops, played as shorts "
                        "however long they run")
    p.add_argument("--state-dir", type=Path, default=config.get("state_dir"),
                   help="Where the duration cache is stored")
    p.add_argument("--metadata-dir", type=Path, default=main_player.get("metadata_dir"),
                   help="Metadata sidecar root; when set, version families come "
                        "from Evolver's sidecars instead of clip names")
    p.add_argument("--notice-file", type=Path, default=None,
                   help="Where to publish one-shot notices for the Fun Time overlay")
    p.add_argument("--playlist", type=Path, default=None,
                   help="Video/funscript pair file: the selection Fun Time made")
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=900)
    p.add_argument("--x", type=int, default=None)
    p.add_argument("--y", type=int, default=None)
    p.add_argument("--tcode-host", default=main_player.get("tcode_udp_host", "127.0.0.1"))
    p.add_argument("--tcode-port", type=int,
                   default=main_player.get("tcode_udp_port", ports.TCODE_UDP))
    p.add_argument("--command-file", type=Path, default=None,
                   help="Poll this file for orchestrator commands")
    p.add_argument("--paused-file", type=Path, default=None,
                   help="Flag file that owns the paused state when present")
    p.add_argument("--status-file", type=Path, default=None,
                   help="Publish playback status to this file")
    p.add_argument("--console-file", type=Path, default=None,
                   help="Poll this file for the console panel Fun Time publishes")
    p.add_argument("--drive-file", type=Path, default=None,
                   help="Poll this file for the OSR2 readout Genau publishes")
    p.add_argument("--dashboard-cmd-file", type=Path, default=None,
                   help="Where a press on the console or the volume control posts "
                        "its Fun Time command")
    p.add_argument("--no-audio", action="store_true", default=False,
                   help="Never extract or play audio (silent)")
    p.add_argument("--taskbar-identity", default=None,
                   help="Group this window under Fun Time's taskbar button: its "
                        "AppUserModelID")
    p.add_argument("--icon", type=Path, default=None,
                   help="The window icon Fun Time hands over, so an Alt-Tab entry "
                        "says whose window this is")
    return p


def audio_muted(args) -> bool:
    """Silent by ``--no-audio``, the ``FUN_TIME_MUTE_AUDIO`` contract, or being
    off-screen -- so a hidden-desktop run is inaudible however it was launched."""
    import os

    return (bool(args.no_audio)
            or os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"
            or on_hidden_desktop())


def _state_path(args, name: str) -> Path:
    """A file in the main player's state dir; beside its config when none is
    configured, and in the working directory when there is no config either."""
    if args.state_dir:
        base = Path(args.state_dir)
    elif args.config is not None:
        base = Path(args.config).resolve().parent
    else:
        base = Path.cwd()
    return base / name


def _duration_cache_path(args) -> Path:
    return _state_path(args, "main_player_durations.json")


def mode_memory(args) -> ModeMemory:
    """Where the main player writes down the mode it is in — the length filter and any
    compilation — so the next session, which Fun Time opens on this one's resumed
    playlist, can name it and re-enter the compilation."""
    return ModeMemory(_state_path(args, "main_player_mode.txt"))


def play_points(args) -> PlayPoints:
    return PlayPoints(_state_path(args, play_points_filename("main_player")))


def library_source(
    args,
    *,
    rng: random.Random | None = None,
    durations: dict[Path, float] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> LibrarySource | None:
    """Build the :class:`LibrarySource`, or None when the library dirs are absent.

    Built whenever ``--videos-dir``/``--scripts-dir`` are known — including under
    Fun Time, which passes its own ``--playlist`` for the *initial* selection but
    still needs this source for version cycling and the shorts/full-length
    toggle.  *durations* is a test seam; production probes via the cache.
    *on_progress* goes straight through to the build, where the wait is.
    """
    if args.videos_dir is None or args.scripts_dir is None:
        return None
    return build_library_source(
        Path(args.videos_dir),
        Path(args.scripts_dir),
        Path(args.clips_dir) if args.clips_dir else None,
        rng=rng or random.Random(),
        duration_cache=None if durations is not None else DurationCache(_duration_cache_path(args)),
        durations=durations,
        metadata_root=Path(args.metadata_dir) if args.metadata_dir else None,
        on_progress=on_progress,
    )


def resolve_playlist(
    args, *, source: LibrarySource | None = None,
) -> list[PlaylistItem]:
    """The playlist Fun Time passed, collapsed to one entry per version group.

    Fun Time lists every version of every video; a library *source*, when
    present, folds those into one slot each (matching the set "cycle version"
    walks).  Without a source — no library dirs — the file is returned verbatim,
    since the main player then has no grouping to apply.
    """
    items = read_playlist(Path(args.playlist))
    if source is not None:
        items = collapse_playlist_versions(items, source.version_index)
    return items
