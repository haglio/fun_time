from __future__ import annotations

import numpy as np

from .frame_store import safe_frame
from .playback import current_loop_frame_index
from .render_canvas import build_canvas
from .render_overlays import (
    draw_exit_overlay as render_exit_overlay,
    draw_export_overlay as render_export_overlay,
)
from .render_primitives import (
    draw_button,
    draw_dotted_vertical_line,
    draw_progress_bar,
    put_text,
    put_text_centered,
    scale_to_fit,
)
from .state import VideoState


def build_ui(state: VideoState) -> np.ndarray:
    current_frame = safe_frame(state, state.current)
    loop_idx = current_loop_frame_index(state)
    loop_frame = safe_frame(state, loop_idx)
    return build_canvas(
        state,
        current_frame,
        loop_frame,
        loop_idx,
        draw_button=draw_button,
        draw_dotted_vertical_line=draw_dotted_vertical_line,
        draw_exit_overlay=draw_exit_overlay,
        draw_export_overlay=draw_export_overlay,
        put_text=put_text,
        put_text_centered=put_text_centered,
        scale_to_fit=scale_to_fit,
    )


def draw_export_overlay(canvas: np.ndarray, state: VideoState) -> None:
    render_export_overlay(
        canvas,
        state,
        draw_button=draw_button,
        draw_progress_bar=draw_progress_bar,
        put_text=put_text,
    )


def draw_exit_overlay(canvas: np.ndarray, state: VideoState) -> None:
    render_exit_overlay(
        canvas,
        state,
        draw_button=draw_button,
        put_text=put_text,
    )
