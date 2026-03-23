from __future__ import annotations

import math

import cv2
import numpy as np

from .loop_modes import (
    LOOP_MODE_BASE_TIP,
    LOOP_MODE_BASE_TIP_BASE,
    LOOP_MODE_TIP_BASE,
    LOOP_MODE_TIP_BASE_TIP,
)


def smoothstep01(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def ease_cos01(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


def blend_pair(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    out = (1.0 - t) * a.astype(np.float32) + t * b.astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def flow_for_pair(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a_gray = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    b_gray = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    flow_ab = cv2.calcOpticalFlowFarneback(
        a_gray, b_gray, None,
        pyr_scale=0.5, levels=3, winsize=25,
        iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
    )
    flow_ba = cv2.calcOpticalFlowFarneback(
        b_gray, a_gray, None,
        pyr_scale=0.5, levels=3, winsize=25,
        iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
    )
    return flow_ab, flow_ba


def remap_with_flow(img: np.ndarray, flow: np.ndarray, factor: float) -> np.ndarray:
    h, w = img.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    map_x = grid_x - factor * flow[..., 0]
    map_y = grid_y - factor * flow[..., 1]
    return cv2.remap(
        img,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


def build_bridge(last_frame: np.ndarray, first_frame: np.ndarray, bridge_frames: int, mode: str) -> list[np.ndarray]:
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


def build_symmetric_blend(frames: list[np.ndarray], seam_frames: int) -> list[np.ndarray]:
    n = len(frames)
    out = [frame.copy() for frame in frames]
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


def resize_frames(frames: list[np.ndarray], scale: float) -> list[np.ndarray]:
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


def shift_frames_halfway(frames: list[np.ndarray]) -> list[np.ndarray]:
    if len(frames) < 2:
        return list(frames)
    shift = max(1, len(frames) // 2)
    return list(frames[shift:]) + list(frames[:shift])


def normalize_loop_mode(frames: list[np.ndarray], loop_mode: str) -> list[np.ndarray]:
    if loop_mode == LOOP_MODE_BASE_TIP_BASE:
        return [frame.copy() for frame in frames]
    if loop_mode == LOOP_MODE_TIP_BASE_TIP:
        return [frame.copy() for frame in shift_frames_halfway(frames)]
    if loop_mode == LOOP_MODE_BASE_TIP:
        return [frame.copy() for frame in frames] + [frame.copy() for frame in frames[-2::-1]]
    if loop_mode == LOOP_MODE_TIP_BASE:
        reversed_frames = list(reversed(frames))
        return [frame.copy() for frame in reversed_frames[:-1]] + [frame.copy() for frame in frames]
    raise RuntimeError(f"Unsupported loop mode: {loop_mode}")
