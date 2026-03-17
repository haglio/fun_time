from __future__ import annotations

import argparse
import socket
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
    return ap


def find_audio(audio_folder: Path, stem: str) -> Path | None:
    for ext in SUPPORTED_EXTS:
        path = audio_folder / f"{stem}{ext}"
        if path.exists():
            return path
    return None


def main(argv: list[str] | None = None) -> int:
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.robot_hand_audio", config.log_file("robot_hand_audio"))
    install_exception_logging(logger)
    args = build_parser(config).parse_args(argv)

    audio_folder = Path(args.audio_folder)
    if not audio_folder.exists():
        raise RuntimeError(f"Audio folder does not exist: {audio_folder}")

    pygame.mixer.init()
    logger.info("Audio companion listening on %s:%s", args.host, args.port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))

    current_path: Path | None = None
    visible = False
    paused = False

    def apply_state() -> None:
        nonlocal paused

        if visible and current_path is not None:
            if pygame.mixer.music.get_busy():
                if paused:
                    pygame.mixer.music.unpause()
                    paused = False
            else:
                pygame.mixer.music.play(-1)
                paused = False
        else:
            if pygame.mixer.music.get_busy() and not paused:
                pygame.mixer.music.pause()
                paused = True

    try:
        while True:
            data, _addr = sock.recvfrom(4096)
            line = data.decode("utf-8", errors="replace").strip()

            if line.startswith("CLIP "):
                stem = line[5:].strip()
                current_path = find_audio(audio_folder, stem)

                if current_path is not None:
                    logger.info("Switching audio clip to %s", current_path.name)
                    pygame.mixer.music.load(str(current_path))
                    paused = False
                    if visible:
                        pygame.mixer.music.play(-1)
                else:
                    logger.warning("No audio file found for stem %s", stem)
                    pygame.mixer.music.stop()
                    paused = False

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