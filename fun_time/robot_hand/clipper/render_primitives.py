from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

Rect = tuple[int, int, int, int]
Color = tuple[int, int, int]
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\CascadiaMono.ttf"),
    Path(r"C:\Windows\Fonts\consola.ttf"),
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
)
DEFAULT_FONT_PATH = next((path for path in FONT_CANDIDATES if path.exists()), None)
FALLBACK_TEXT_FONT = cv2.FONT_HERSHEY_DUPLEX


def _font_px(scale: float) -> int:
    return max(10, int(round(scale * 28)))


@lru_cache(maxsize=32)
def _get_font(size_px: int) -> ImageFont.FreeTypeFont | None:
    if DEFAULT_FONT_PATH is None:
        return None
    return ImageFont.truetype(str(DEFAULT_FONT_PATH), size_px)


@lru_cache(maxsize=512)
def _text_bbox(text: str, size_px: int) -> tuple[int, int, int, int] | None:
    font = _get_font(size_px)
    if font is None:
        return None
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    return int(left), int(top), int(right), int(bottom)


def _draw_text_fallback_cv(
    img: np.ndarray,
    text: str,
    x: int,
    y: int,
    scale: float,
    color: Color,
    thickness: int,
) -> None:
    cv2.putText(img, text, (int(x), int(y)), FALLBACK_TEXT_FONT, scale, color, thickness, cv2.LINE_AA)


@lru_cache(maxsize=512)
def _render_text_patch(text: str, scale: float, color: Color) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    size_px = _font_px(scale)
    font = _get_font(size_px)
    if font is None:
        return None
    bbox = _text_bbox(text, size_px)
    assert bbox is not None
    width = max(1, int(bbox[2] - bbox[0]))
    height = max(1, int(bbox[3] - bbox[1]))
    patch = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(patch)
    draw.text((-bbox[0], -bbox[1]), text, font=font, anchor="ls", fill=(color[2], color[1], color[0], 255))
    return np.asarray(patch, dtype=np.uint8), bbox


def scale_to_fit(img: np.ndarray, max_w: int, max_h: int) -> np.ndarray:
    h, w = img.shape[:2]
    if h <= max_h and w <= max_w:
        return img
    scale = min(max_w / w, max_h / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def text_bbox(text: str, scale: float = 0.6, thickness: int = 1) -> tuple[int, int, int, int]:
    bbox = _text_bbox(text, _font_px(scale))
    if bbox is not None:
        return bbox
    (width, height), baseline = cv2.getTextSize(text, FALLBACK_TEXT_FONT, scale, thickness)
    return 0, -int(height), int(width), int(baseline)


def put_text(
    img: np.ndarray,
    text: str,
    x: int,
    y: int,
    scale: float = 0.6,
    color: Color = (230, 230, 230),
    thickness: int = 1,
) -> None:
    rendered = _render_text_patch(text, scale, color)
    if rendered is None:
        _draw_text_fallback_cv(img, text, x, y, scale, color, thickness)
        return
    patch, bbox = rendered
    text_h = patch.shape[0]
    x1 = int(x + bbox[0])
    y1 = int(y + bbox[1])
    x2 = min(img.shape[1], x1 + patch.shape[1])
    y2 = min(img.shape[0], y1 + patch.shape[0])
    src_x1 = max(0, -x1)
    src_y1 = max(0, -y1)
    x1 = max(0, x1)
    y1 = max(0, y1)
    if x1 >= x2 or y1 >= y2:
        return
    src = patch[src_y1:src_y1 + (y2 - y1), src_x1:src_x1 + (x2 - x1)]
    alpha = src[:, :, 3:4].astype(np.float32) / 255.0
    dst = img[y1:y2, x1:x2].astype(np.float32)
    src_bgr = src[:, :, :3].astype(np.float32)
    img[y1:y2, x1:x2] = np.clip(src_bgr * alpha + dst * (1.0 - alpha), 0, 255).astype(np.uint8)


def text_wh(text: str, scale: float = 0.6, thickness: int = 1) -> tuple[int, int]:
    left, top, right, bottom = text_bbox(text, scale, thickness)
    return int(right - left), int(bottom - top)


def put_text_centered(
    img: np.ndarray,
    text: str,
    center_x: int,
    y: int,
    scale: float = 0.6,
    color: Color = (230, 230, 230),
    thickness: int = 1,
) -> None:
    tw, _ = text_wh(text, scale, thickness)
    put_text(img, text, center_x - tw // 2, y, scale, color, thickness)


def draw_button(
    img: np.ndarray,
    rect: Rect,
    text: str,
    enabled: bool = True,
    active: bool = False,
    fill_color: Color | None = None,
    active_fill_color: Color | None = None,
    icon: str | None = None,
    focused: bool = False,
    focus_border_color: Color = (110, 220, 255),
    focus_border_thickness: int = 3,
) -> None:
    x1, y1, x2, y2 = map(int, rect)
    fill = fill_color if fill_color is not None else ((62, 62, 62) if enabled else (40, 40, 40))
    if active and enabled:
        fill = active_fill_color if active_fill_color is not None else (80, 90, 130)
    if not enabled and fill_color is not None:
        fill = tuple(max(20, int(c * 0.55)) for c in fill_color)
    border = (210, 210, 210) if enabled else (95, 95, 95)
    color = (240, 240, 240) if enabled else (120, 120, 120)
    cv2.rectangle(img, (x1, y1), (x2, y2), fill, -1)
    cv2.rectangle(img, (x1, y1), (x2, y2), border, 1)
    if focused and enabled:
        cv2.rectangle(img, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), focus_border_color, focus_border_thickness)
    if icon == "play":
        width = x2 - x1
        height = y2 - y1
        tri_h = max(12, int(round(height * 0.5)))
        tri_w = max(10, int(round(tri_h * 0.7)))
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        left_x = cx - tri_w // 2
        top_y = cy - tri_h // 2
        bottom_y = cy + tri_h // 2
        pts = np.array(
            [
                (left_x, top_y),
                (left_x, bottom_y),
                (left_x + tri_w, cy),
            ],
            dtype=np.int32,
        )
        cv2.fillConvexPoly(img, pts, color)
        return
    if icon == "pause":
        bar_w = max(5, (x2 - x1) // 8)
        bar_h_margin = max(6, (y2 - y1) // 5)
        gap = max(6, bar_w)
        center_x = (x1 + x2) // 2
        left_x1 = center_x - gap // 2 - bar_w
        right_x1 = center_x + gap // 2
        cv2.rectangle(img, (left_x1, y1 + bar_h_margin), (left_x1 + bar_w, y2 - bar_h_margin), color, -1)
        cv2.rectangle(img, (right_x1, y1 + bar_h_margin), (right_x1 + bar_w, y2 - bar_h_margin), color, -1)
        return
    ts = 0.7
    left, top, right, bottom = text_bbox(text, ts, 2)
    tw = right - left
    th = bottom - top
    tx = x1 + max(0, (x2 - x1 - tw) // 2) - left
    ty = y1 + max(0, (y2 - y1 - th) // 2) - top
    put_text(img, text, tx, ty, ts, color, 2)


def draw_dotted_vertical_line(
    img: np.ndarray,
    x: int,
    y1: int,
    y2: int,
    color: Color,
    *,
    segment: int = 6,
    gap: int = 4,
    thickness: int = 1,
) -> None:
    y = y1
    while y <= y2:
        seg_end = min(y2, y + segment)
        cv2.line(img, (x, y), (x, seg_end), color, thickness)
        y = seg_end + gap


def draw_progress_bar(
    img: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    p: float,
    color: Color = (110, 210, 110),
    label: str = "",
) -> None:
    cv2.rectangle(img, (x, y), (x + w, y + h), (215, 215, 215), 1)
    fill = max(0, min(w, int(round(w * max(0.0, min(1.0, p))))))
    if fill > 0:
        cv2.rectangle(img, (x + 1, y + 1), (x + fill - 1, y + h - 1), color, -1)
    if label:
        left, top, right, bottom = text_bbox(label, 0.5, 1)
        tw = right - left
        th = bottom - top
        tx = x + (w - tw) // 2 - left
        ty = y + (h - th) // 2 - top
        put_text(img, label, tx, ty, 0.5, (240, 240, 240), 1)
