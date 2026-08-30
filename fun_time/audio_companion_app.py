from __future__ import annotations

import argparse
import logging
import math
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pygame

from .audio_companion_runtime import AudioCompanionRuntime
from .audio_volume import MAX_VOLUME, read_volume
from .config import load_config
from player_core.file_channel import read_paused_state

from app_support.cli import preparse_config_path
from app_support.logging_utils import configure_logging, install_exception_logging

SUPPORTED_EXTS = [".mp3", ".wav", ".ogg", ".flac", ".m4a"]


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Play Genau companion audio.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--audio-folder", default=str(config.paths.audio_dir))
    ap.add_argument("--host", default=config.audio_companion.host)
    ap.add_argument("--port", type=int, default=config.audio_companion.port)
    ap.add_argument("--mode-file", default=str(config.genau_mode_file))
    ap.add_argument("--paused-file", default=str(config.audio_paused_file))
    ap.add_argument("--volume-file", default=str(config.audio_volume_file))
    return ap


def force_muted() -> bool:
    """Whether this run must stay silent whatever the bridge publishes."""
    return os.environ.get("FUN_TIME_MUTE_AUDIO") == "1"


def find_audio(audio_folder: Path, stem: str) -> Path | None:
    for ext in SUPPORTED_EXTS:
        path = audio_folder / f"{stem}{ext}"
        if path.exists():
            return path
    return None


@dataclass
class AudioPlaybackController:
    """The one clip the companion is playing, and where it had got to.

    The sound is handed in, not reached for: driving the ``pygame.mixer.music``
    global meant this could not be built without the audio subsystem in
    whatever state the process had left it.
    """

    audio_folder: Path
    logger: logging.Logger
    music: Any
    sound_length: Callable[[Path], float | None]
    # Hidden and integration runs set FUN_TIME_MUTE_AUDIO; they stay silent no
    # matter what level the bridge publishes.
    force_muted: bool = False
    volume: int = MAX_VOLUME
    current_path: Path | None = None
    visible: bool = False
    mode_active: bool = False
    paused: bool = False
    manual_paused: bool = False
    playback_running: bool = False
    play_started_at: float = 0.0
    play_start_position: float = 0.0
    clip_positions: dict[Path, float] = field(default_factory=dict)
    clip_lengths: dict[Path, float | None] = field(default_factory=dict)

    def get_clip_length(self, path: Path) -> float | None:
        if path in self.clip_lengths:
            return self.clip_lengths[path]

        try:
            length = self.sound_length(path)
            if length is not None and (not math.isfinite(length) or length <= 0):
                length = None
        except Exception:
            length = None

        self.clip_lengths[path] = length
        return length

    def normalize_position(self, path: Path, value: float) -> float:
        if value < 0:
            return 0.0

        length = self.get_clip_length(path)
        if length is None:
            return value

        if length <= 0:
            return 0.0

        return value % length

    def current_position_for_active_clip(self) -> float:
        if self.current_path is None:
            return 0.0

        if self.playback_running:
            elapsed = max(0.0, time.monotonic() - self.play_started_at)
            return self.normalize_position(self.current_path, self.play_start_position + elapsed)

        return self.normalize_position(self.current_path, self.play_start_position)

    def save_active_clip_position(self) -> None:
        if self.current_path is None:
            return

        position = self.current_position_for_active_clip()
        self.clip_positions[self.current_path] = position
        self.play_start_position = position

    def play_current_clip_from_saved_position(self) -> None:
        if self.current_path is None:
            return

        start_position = self.normalize_position(self.current_path, self.clip_positions.get(self.current_path, 0.0))
        try:
            self.music.play(-1, start=start_position)
        except TypeError:
            self.music.play(-1)
            if start_position > 0:
                try:
                    self.music.set_pos(start_position)
                except Exception:
                    start_position = 0.0
        except Exception:
            self.music.play(-1)
            start_position = 0.0

        self.play_start_position = start_position
        self.play_started_at = time.monotonic()
        self.playback_running = True
        self.paused = False

    def apply_state(self) -> None:
        should_play = self.mode_active and self.visible and self.current_path is not None and not self.manual_paused

        if should_play:
            if self.music.get_busy():
                if self.paused:
                    self.music.unpause()
                    self.play_started_at = time.monotonic()
                    self.playback_running = True
                    self.paused = False
            else:
                self.play_current_clip_from_saved_position()
        else:
            if self.music.get_busy() and not self.paused:
                self.save_active_clip_position()
                self.music.pause()
                self.playback_running = False
                self.paused = True

    def switch_clip(self, path: Path | None) -> None:
        if self.current_path is not None:
            self.save_active_clip_position()

        self.current_path = path

        if self.current_path is None:
            self.music.stop()
            self.paused = False
            self.playback_running = False
            self.play_start_position = 0.0
            return

        self.music.load(str(self.current_path))
        self.play_start_position = self.normalize_position(self.current_path, self.clip_positions.get(self.current_path, 0.0))
        self.playback_running = False
        self.paused = False

        self.apply_state()

    def set_manual_paused(self, paused: bool) -> None:
        if self.manual_paused == paused:
            return
        self.manual_paused = paused
        self.apply_state()
        self.logger.info("Audio %s", "paused" if paused else "resumed")

    def set_volume(self, volume: int) -> None:
        """Follow the bridge's published sound level (0-100)."""
        if self.force_muted or volume == self.volume:
            return
        self.volume = volume
        self.music.set_volume(volume / MAX_VOLUME)

    def set_mode_active(self, active: bool) -> None:
        self.mode_active = active
        self.apply_state()

    def handle_udp_line(self, line: str) -> None:
        if line.startswith("CLIP "):
            stem = line[5:].strip()
            path = find_audio(self.audio_folder, stem)

            if path is not None:
                self.logger.info("Switching audio clip to %s", path.name)
                self.switch_clip(path)
            else:
                self.logger.warning("No audio file found for stem %s", stem)
                self.switch_clip(None)

        elif line == "VISIBLE 1":
            self.visible = True
            self.apply_state()

        elif line == "VISIBLE 0":
            self.visible = False
            self.apply_state()


def main(argv: list[str] | None = None) -> int:
    config = load_config(preparse_config_path(argv))
    logger = configure_logging("fun_time.genau_audio", config.log_file("genau_audio"))
    install_exception_logging(logger)
    args = build_parser(config).parse_args(argv)

    audio_folder = Path(args.audio_folder)
    if not audio_folder.exists():
        raise RuntimeError(f"Audio folder does not exist: {audio_folder}")

    mode_file = Path(args.mode_file)
    paused_file = Path(args.paused_file)
    volume_file = Path(args.volume_file)

    pygame.mixer.init()
    muted = force_muted()
    if muted:
        pygame.mixer.music.set_volume(0)
        logger.info("Audio muted (FUN_TIME_MUTE_AUDIO=1)")
    logger.info("Audio companion listening on %s:%s", args.host, args.port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.15)

    # Both flag files, read the way every other player reads its own.
    def read_flag(path: Path) -> bool:
        return read_paused_state(path, logger=logger)

    controller = AudioPlaybackController(
        audio_folder=audio_folder,
        logger=logger,
        music=pygame.mixer.music,
        sound_length=lambda path: float(pygame.mixer.Sound(str(path)).get_length()),
        force_muted=muted,
    )
    runtime = AudioCompanionRuntime(
        sock=sock,
        controller=controller,
        mode_file=mode_file,
        read_mode_active=read_flag,
        paused_file=paused_file,
        read_paused_state=read_flag,
        volume_file=volume_file,
        read_volume=read_volume,
    )

    try:
        runtime.run_forever()
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
