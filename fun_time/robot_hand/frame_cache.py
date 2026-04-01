from __future__ import annotations

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np


def write_rhcache(
    frames: list[np.ndarray],
    output_path: Path,
    *,
    source_name: str = "",
    quality: int = 95,
    lossless: bool = False,
) -> None:
    if not frames:
        raise ValueError("frames list is empty")

    height, width = frames[0].shape[:2]
    meta = {
        "width": width,
        "height": height,
        "frame_count": len(frames),
        "source": source_name,
    }

    encode_quality = 101 if lossless else quality

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("meta.json", json.dumps(meta))
        for i, frame in enumerate(frames):
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(
                ".webp", bgr, [cv2.IMWRITE_WEBP_QUALITY, encode_quality]
            )
            if not ok:
                raise RuntimeError(f"WebP encode failed for frame {i}")
            zf.writestr(f"frames/{i:06d}.webp", buf.tobytes())


def read_rhcache_meta(cache_path: Path) -> dict:
    with zipfile.ZipFile(cache_path, "r") as zf:
        return json.loads(zf.read("meta.json"))


def _decode_webp_rgb(buf: bytes) -> np.ndarray:
    arr = np.frombuffer(buf, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def read_rhcache_all_frames(cache_path: Path) -> list[np.ndarray]:
    meta = read_rhcache_meta(cache_path)
    frames: list[np.ndarray] = []
    with zipfile.ZipFile(cache_path, "r") as zf:
        for i in range(meta["frame_count"]):
            frames.append(_decode_webp_rgb(zf.read(f"frames/{i:06d}.webp")))
    return frames


def read_rhcache_frame(cache_path: Path, index: int) -> np.ndarray:
    with zipfile.ZipFile(cache_path, "r") as zf:
        buf = zf.read(f"frames/{index:06d}.webp")
    return _decode_webp_rgb(buf)
