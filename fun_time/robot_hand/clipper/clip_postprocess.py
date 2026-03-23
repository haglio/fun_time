from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from fractions import Fraction

import cv2
import numpy as np

from .loop_modes import (
    LOOP_MODE_BASE_TIP,
    LOOP_MODE_BASE_TIP_BASE,
    LOOP_MODE_TIP_BASE,
    LOOP_MODE_TIP_BASE_TIP,
    LOOP_MODES,
)

def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)


def ffprobe_video(path):
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
    s = data["streams"][0]

    def frac_to_float(v):
        if not v or v == "0/0":
            return None
        return float(Fraction(v))

    fps = frac_to_float(s.get("avg_frame_rate")) or frac_to_float(s.get("r_frame_rate"))
    if not fps or fps <= 0:
        raise RuntimeError("Could not determine FPS via ffprobe.")

    width = int(s["width"])
    height = int(s["height"])
    nb_frames = s.get("nb_frames")
    nb_frames = int(nb_frames) if nb_frames and str(nb_frames).isdigit() else None
    duration = float(s["duration"]) if s.get("duration") else None

    return {
        "fps": fps,
        "width": width,
        "height": height,
        "nb_frames": nb_frames,
        "duration": duration,
    }


def read_frames(path):
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


def smoothstep01(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def ease_cos01(x):
    x = max(0.0, min(1.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


def blend_pair(a, b, t):
    out = (1.0 - t) * a.astype(np.float32) + t * b.astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def flow_for_pair(a, b):
    a_gray = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    b_gray = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    flow_ab = cv2.calcOpticalFlowFarneback(
        a_gray, b_gray, None,
        pyr_scale=0.5, levels=3, winsize=25,
        iterations=3, poly_n=5, poly_sigma=1.2, flags=0
    )
    flow_ba = cv2.calcOpticalFlowFarneback(
        b_gray, a_gray, None,
        pyr_scale=0.5, levels=3, winsize=25,
        iterations=3, poly_n=5, poly_sigma=1.2, flags=0
    )
    return flow_ab, flow_ba


def remap_with_flow(img, flow, factor):
    h, w = img.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    map_x = grid_x - factor * flow[..., 0]
    map_y = grid_y - factor * flow[..., 1]
    return cv2.remap(
        img,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT
    )


def build_bridge(last_frame, first_frame, bridge_frames, mode):
    if bridge_frames <= 0:
        return []
    bridge = []

    if mode == "flow":
        flow_ab, flow_ba = flow_for_pair(last_frame, first_frame)

    for i in range(bridge_frames):
        t = (i + 1) / (bridge_frames + 1)
        t_eased = ease_cos01(t)

        if mode == "flow":
            a_warp = remap_with_flow(last_frame, flow_ab, t_eased)
            b_warp = remap_with_flow(first_frame, flow_ba, 1.0 - t_eased)
            frame = blend_pair(a_warp, b_warp, t_eased)
        else:
            frame = blend_pair(last_frame, first_frame, t_eased)

        bridge.append(frame)

    return bridge


def build_symmetric_blend(frames, seam_frames):
    n = len(frames)
    out = [f.copy() for f in frames]
    for i in range(seam_frames):
        t = smoothstep01((i + 1) / seam_frames)
        start_idx = i
        end_idx = n - seam_frames + i

        start_f = frames[start_idx]
        end_f = frames[end_idx]

        midpoint = blend_pair(end_f, start_f, 0.5)
        out[start_idx] = blend_pair(start_f, midpoint, t)
        out[end_idx] = blend_pair(end_f, midpoint, t)
    return out


def encode_with_ffmpeg(frames, fps, out_path, crf, preset, pix_fmt, input_audio_path=None):
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


def resize_frames(frames, scale):
    if scale >= 0.999:
        return frames

    h, w = frames[0].shape[:2]
    new_w = max(2, int(round(w * scale)))
    new_h = max(2, int(round(h * scale)))

    if new_w % 2:
        new_w -= 1
    if new_h % 2:
        new_h -= 1

    return [cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA) for frame in frames]


def shift_frames_halfway(frames):
    if len(frames) < 2:
        return list(frames)
    shift = max(1, len(frames) // 2)
    return list(frames[shift:]) + list(frames[:shift])


def normalize_loop_mode(frames, loop_mode):
    if loop_mode == LOOP_MODE_BASE_TIP_BASE:
        return [f.copy() for f in frames]
    if loop_mode == LOOP_MODE_TIP_BASE_TIP:
        return [f.copy() for f in shift_frames_halfway(frames)]
    if loop_mode == LOOP_MODE_BASE_TIP:
        return [f.copy() for f in frames] + [f.copy() for f in frames[-2::-1]]
    if loop_mode == LOOP_MODE_TIP_BASE:
        reversed_frames = list(reversed(frames))
        return [f.copy() for f in reversed_frames[:-1]] + [f.copy() for f in frames]
    raise RuntimeError(f"Unsupported loop mode: {loop_mode}")


def main():
    ap = argparse.ArgumentParser(
        description="Normalize clip loop shape, smooth the seam, and shrink the output if needed."
    )
    ap.add_argument("input", help="Input video path")
    ap.add_argument("-o", "--output", required=True, help="Output video path")
    ap.add_argument(
        "--loop-mode",
        choices=LOOP_MODES,
        default=LOOP_MODE_BASE_TIP_BASE,
        help="How to normalize the exported clip before smoothing the seam",
    )
    ap.add_argument("--bridge-ms", type=float, default=80.0, help="Bridge length in milliseconds (default: 80)")
    ap.add_argument("--bridge-frames", type=int, default=None, help="Bridge length in frames (overrides --bridge-ms)")
    ap.add_argument("--mode", choices=["flow", "blend"], default="flow", help="Bridge generation mode (default: flow)")
    length_group = ap.add_mutually_exclusive_group()
    length_group.add_argument("--keep-length", dest="keep_length", action="store_true", help="Replace the tail with the bridge instead of appending it (default)")
    length_group.add_argument("--append", dest="keep_length", action="store_false", help="Append the bridge to the end instead of replacing the tail")
    ap.set_defaults(keep_length=True)
    ap.add_argument("--symmetric-blend", type=int, default=0, help="Also softly pull the first/last N frames toward each other before bridge generation")
    ap.add_argument("--copy-audio", action="store_true", help="Try to keep input audio (usually not ideal for seamless loops)")
    ap.add_argument("--crf", type=int, default=12, help="libx264 CRF (default: 12)")
    ap.add_argument("--preset", default="slow", help="libx264 preset (default: slow)")
    ap.add_argument("--pix-fmt", default="yuv420p", help="Output pixel format (default: yuv420p)")
    ap.add_argument("--max-mb", type=float, default=1.0, help="Maximum output file size in MB (default: 1.0)")
    args = ap.parse_args()

    if args.max_mb <= 0:
        raise RuntimeError("--max-mb must be greater than 0.")

    max_output_size_bytes = int(args.max_mb * 1024 * 1024)

    meta = ffprobe_video(args.input)
    fps = meta["fps"]

    frames = read_frames(args.input)
    n = len(frames)
    if n < 3:
        raise RuntimeError("Clip is too short.")

    work_frames = normalize_loop_mode(frames, args.loop_mode)
    normalized_n = len(work_frames)
    bridge_frames = args.bridge_frames
    if bridge_frames is None:
        bridge_frames = max(1, int(round(fps * (args.bridge_ms / 1000.0))))
    max_bridge = max(1, normalized_n // 3)
    bridge_frames = max(1, min(bridge_frames, max_bridge))

    if args.symmetric_blend > 0:
        seam_frames = min(args.symmetric_blend, max(1, normalized_n // 4))
        work_frames = build_symmetric_blend(work_frames, seam_frames)

    bridge = build_bridge(work_frames[-1], work_frames[0], bridge_frames, args.mode)

    if args.keep_length:
        if bridge_frames >= len(work_frames):
            raise RuntimeError("--keep-length bridge is too long for this clip.")
        out_frames = work_frames[:-bridge_frames] + bridge
    else:
        out_frames = work_frames + bridge

    scale = 1.0
    min_dim = 64
    attempt = 0
    while True:
        attempt += 1
        frames_to_encode = resize_frames(out_frames, scale)
        encode_with_ffmpeg(
            frames_to_encode,
            fps,
            args.output,
            args.crf,
            args.preset,
            args.pix_fmt,
            input_audio_path=args.input if args.copy_audio else None
        )

        size_bytes = os.path.getsize(args.output)
        if size_bytes <= max_output_size_bytes:
            break

        h, w = frames_to_encode[0].shape[:2]
        if min(h, w) <= min_dim:
            print(f"Warning: output is still >{args.max_mb:g} MB at minimum allowed resolution.")
            break

        scale *= 0.9

    print(f"Input FPS: {fps:.6f}")
    print(f"Input frames: {n}")
    print(f"Loop mode: {args.loop_mode}")
    print(f"Normalized frames: {normalized_n}")
    print(f"Bridge frames: {bridge_frames}")
    print(f"Output frames: {len(out_frames)}")
    print(f"Encode attempts: {attempt}")
    print(f"Final scale: {scale:.4f}")
    print(f"Final size (bytes): {os.path.getsize(args.output)}")
    print(f"Target max size (MB): {args.max_mb:g}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
