import argparse
import socket
from pathlib import Path

import pygame

SUPPORTED_EXTS = [".mp3", ".wav", ".ogg", ".flac", ".m4a"]

ap = argparse.ArgumentParser()
ap.add_argument("--audio-folder", required=True)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=50556)
args = ap.parse_args()

audio_folder = Path(args.audio_folder)
if not audio_folder.exists():
    raise RuntimeError(f"Audio folder does not exist: {audio_folder}")

pygame.mixer.init()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((args.host, args.port))

current_stem = None
current_path = None
visible = False
paused = False

def find_audio(stem: str):
    for ext in SUPPORTED_EXTS:
        p = audio_folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None

def apply_state():
    global paused

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

while True:
    data, _addr = sock.recvfrom(4096)
    line = data.decode("utf-8", errors="replace").strip()

    if line.startswith("CLIP "):
        stem = line[5:].strip()
        path = find_audio(stem)
        current_stem = stem
        current_path = path

        if path is not None:
            pygame.mixer.music.load(str(path))
            paused = False
            if visible:
                pygame.mixer.music.play(-1)
        else:
            pygame.mixer.music.stop()
            paused = False

    elif line == "VISIBLE 1":
        visible = True
        apply_state()

    elif line == "VISIBLE 0":
        visible = False
        apply_state()