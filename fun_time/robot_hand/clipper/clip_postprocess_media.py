from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction

import cv2


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)


def ffprobe_video(path: str) -> dict[str, float | int | None]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height,nb_frames,duration",
        "-of", "json",
        path,
    ]
    data = json.loads(run(cmd).stdout)
    if not data.get("streams"):
        raise RuntimeError("No video stream found.")
    stream = data["streams"][0]

    def frac_to_float(value: str | None) -> float | None:
        if not value or value == "0/0":
            return None
        return float(Fraction(value))

    fps = frac_to_float(stream.get("avg_frame_rate")) or frac_to_float(stream.get("r_frame_rate"))
    if not fps or fps <= 0:
        raise RuntimeError("Could not determine FPS via ffprobe.")

    nb_frames = stream.get("nb_frames")
    return {
        "fps": fps,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "nb_frames": int(nb_frames) if nb_frames and str(nb_frames).isdigit() else None,
        "duration": float(stream["duration"]) if stream.get("duration") else None,
    }


def read_frames(path: str) -> list:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError("No frames decoded.")
    return frames


def encode_with_ffmpeg(
    frames: list,
    fps: float,
    out_path: str,
    crf: int,
    preset: str,
    pix_fmt: str,
    input_audio_path: str | None = None,
) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH.")

    h, w = frames[0].shape[:2]
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{w}x{h}",
        "-r", f"{fps:.12f}",
        "-i", "-",
    ]

    if input_audio_path:
        cmd += ["-i", input_audio_path, "-map", "0:v:0", "-map", "1:a:0?", "-shortest"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", pix_fmt,
        out_path,
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in frames:
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg encode failed.\n{stderr}")
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
