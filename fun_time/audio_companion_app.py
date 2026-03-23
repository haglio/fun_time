from __future__ import annotations

import argparse
import logging
import math
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

import pygame

from .config import load_config
from .logging_utils import configure_logging, install_exception_logging
from .runtime_support import consume_command_file as _consume_command_file
from .runtime_support import preparse_config_path

SUPPORTED_EXTS = [".mp3", ".wav", ".ogg", ".flac", ".m4a"]


def _preparse_config(argv: list[str] | None) -> str | None:
    return preparse_config_path(argv)


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Play Robot Hand companion audio.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--audio-folder", default=str(config.paths.audio_dir))
    ap.add_argument("--host", default=config.audio_companion.host)
    ap.add_argument("--port", type=int, default=config.audio_companion.port)
    ap.add_argument("--cmd-file", default=str(config.audio_cmd_file))
    return ap


def find_audio(audio_folder: Path, stem: str) -> Path | None:
    for ext in SUPPORTED_EXTS:
        path = audio_folder / f"{stem}{ext}"
        if path.exists():
            return path
    return None


def consume_command_file(path: Path) -> str | None:
    return _consume_command_file(path)


@dataclass
class AudioPlaybackController:
    audio_folder: Path
    logger: logging.Logger
    current_path: Path | None = None
    visible: bool = False
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
            length = float(pygame.mixer.Sound(str(path)).get_length())
            if not math.isfinite(length) or length <= 0:
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
            pygame.mixer.music.play(-1, start=start_position)
        except TypeError:
            pygame.mixer.music.play(-1)
            if start_position > 0:
                try:
                    pygame.mixer.music.set_pos(start_position)
                except Exception:
                    start_position = 0.0
        except Exception:
            pygame.mixer.music.play(-1)
            start_position = 0.0

        self.play_start_position = start_position
        self.play_started_at = time.monotonic()
        self.playback_running = True
        self.paused = False

    def apply_state(self) -> None:
        should_play = self.visible and self.current_path is not None and not self.manual_paused

        if should_play:
            if pygame.mixer.music.get_busy():
                if self.paused:
                    pygame.mixer.music.unpause()
                    self.play_started_at = time.monotonic()
                    self.playback_running = True
                    self.paused = False
            else:
                self.play_current_clip_from_saved_position()
        else:
            if pygame.mixer.music.get_busy() and not self.paused:
                self.save_active_clip_position()
                pygame.mixer.music.pause()
                self.playback_running = False
                self.paused = True

    def switch_clip(self, path: Path | None) -> None:
        if self.current_path is not None:
            self.save_active_clip_position()

        self.current_path = path

        if self.current_path is None:
            pygame.mixer.music.stop()
            self.paused = False
            self.playback_running = False
            self.play_start_position = 0.0
            return

        pygame.mixer.music.load(str(self.current_path))
        self.play_start_position = self.normalize_position(self.current_path, self.clip_positions.get(self.current_path, 0.0))
        self.playback_running = False
        self.paused = False

        self.apply_state()

    def set_manual_paused(self, paused: bool) -> None:
        self.manual_paused = paused
        self.apply_state()
        self.logger.info("Audio %s", "paused" if paused else "resumed")

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
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.robot_hand_audio", config.log_file("robot_hand_audio"))
    install_exception_logging(logger)
    args = build_parser(config).parse_args(argv)

    audio_folder = Path(args.audio_folder)
    if not audio_folder.exists():
        raise RuntimeError(f"Audio folder does not exist: {audio_folder}")

    cmd_file = Path(args.cmd_file)

    pygame.mixer.init()
    logger.info("Audio companion listening on %s:%s", args.host, args.port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.15)

    controller = AudioPlaybackController(audio_folder=audio_folder, logger=logger)

    try:
        while True:
            line = ""
            try:
                data, _addr = sock.recvfrom(4096)
                line = data.decode("utf-8", errors="replace").strip()
            except socket.timeout:
                pass

            cmd = consume_command_file(cmd_file)
            if cmd == "PAUSE":
                controller.set_manual_paused(True)
            elif cmd == "RESUME":
                controller.set_manual_paused(False)

            if not line:
                continue

            controller.handle_udp_line(line)
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
