from __future__ import annotations

import argparse
import math
import socket
import time
from pathlib import Path

import pygame

from .config import load_config
from .logging_utils import configure_logging, install_exception_logging

SUPPORTED_EXTS = [".mp3", ".wav", ".ogg", ".flac", ".m4a"]


def _preparse_config(argv: list[str] | None) -> str | None:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--config")
    known, _ = ap.parse_known_args(argv)
    return known.config


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
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").replace("\ufeff", "").strip().upper()
        if not text:
            return None
        path.write_text("", encoding="utf-8")
        return text
    except Exception:
        return None


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

    current_path: Path | None = None
    visible = False
    paused = False
    manual_paused = False
    playback_running = False
    play_started_at = 0.0
    play_start_position = 0.0
    clip_positions: dict[Path, float] = {}
    clip_lengths: dict[Path, float | None] = {}

    def get_clip_length(path: Path) -> float | None:
        if path in clip_lengths:
            return clip_lengths[path]

        try:
            length = float(pygame.mixer.Sound(str(path)).get_length())
            if not math.isfinite(length) or length <= 0:
                length = None
        except Exception:
            length = None

        clip_lengths[path] = length
        return length

    def normalize_position(path: Path, value: float) -> float:
        if value < 0:
            return 0.0

        length = get_clip_length(path)
        if length is None:
            return value

        if length <= 0:
            return 0.0

        return value % length

    def current_position_for_active_clip() -> float:
        if current_path is None:
            return 0.0

        if playback_running:
            elapsed = max(0.0, time.monotonic() - play_started_at)
            return normalize_position(current_path, play_start_position + elapsed)

        return normalize_position(current_path, play_start_position)

    def save_active_clip_position() -> None:
        nonlocal play_start_position

        if current_path is None:
            return

        position = current_position_for_active_clip()
        clip_positions[current_path] = position
        play_start_position = position

    def play_current_clip_from_saved_position() -> None:
        nonlocal paused, playback_running, play_started_at, play_start_position

        if current_path is None:
            return

        start_position = normalize_position(current_path, clip_positions.get(current_path, 0.0))
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

        play_start_position = start_position
        play_started_at = time.monotonic()
        playback_running = True
        paused = False

    def apply_state() -> None:
        nonlocal paused, playback_running, play_started_at

        should_play = visible and current_path is not None and not manual_paused

        if should_play:
            if pygame.mixer.music.get_busy():
                if paused:
                    pygame.mixer.music.unpause()
                    play_started_at = time.monotonic()
                    playback_running = True
                    paused = False
            else:
                play_current_clip_from_saved_position()
        else:
            if pygame.mixer.music.get_busy() and not paused:
                save_active_clip_position()
                pygame.mixer.music.pause()
                playback_running = False
                paused = True

    def switch_clip(path: Path | None) -> None:
        nonlocal current_path, paused, playback_running, play_start_position

        if current_path is not None:
            save_active_clip_position()

        current_path = path

        if current_path is None:
            pygame.mixer.music.stop()
            paused = False
            playback_running = False
            play_start_position = 0.0
            return

        pygame.mixer.music.load(str(current_path))
        play_start_position = normalize_position(current_path, clip_positions.get(current_path, 0.0))
        playback_running = False
        paused = False

        apply_state()

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
                manual_paused = True
                apply_state()
                logger.info("Audio paused")
            elif cmd == "RESUME":
                manual_paused = False
                apply_state()
                logger.info("Audio resumed")

            if not line:
                continue

            if line.startswith("CLIP "):
                stem = line[5:].strip()
                path = find_audio(audio_folder, stem)

                if path is not None:
                    logger.info("Switching audio clip to %s", path.name)
                    switch_clip(path)
                else:
                    logger.warning("No audio file found for stem %s", stem)
                    switch_clip(None)

            elif line == "VISIBLE 1":
                visible = True
                apply_state()

            elif line == "VISIBLE 0":
                visible = False
                apply_state()
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())