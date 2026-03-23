from __future__ import annotations

import argparse

from .clip_postprocess_pipeline import postprocess_clip
from .clip_postprocess_transforms import normalize_loop_mode, shift_frames_halfway
from .loop_modes import LOOP_MODE_BASE_TIP_BASE, LOOP_MODES


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
    ap.add_argument(
        "--bridge-ms",
        type=float,
        default=80.0,
        help="Bridge length in milliseconds (default: 80)",
    )
    ap.add_argument(
        "--bridge-frames",
        type=int,
        default=None,
        help="Bridge length in frames (overrides --bridge-ms)",
    )
    ap.add_argument(
        "--mode",
        choices=["flow", "blend"],
        default="flow",
        help="Bridge generation mode (default: flow)",
    )
    length_group = ap.add_mutually_exclusive_group()
    length_group.add_argument(
        "--keep-length",
        dest="keep_length",
        action="store_true",
        help="Replace the tail with the bridge instead of appending it (default)",
    )
    length_group.add_argument(
        "--append",
        dest="keep_length",
        action="store_false",
        help="Append the bridge to the end instead of replacing the tail",
    )
    ap.set_defaults(keep_length=True)
    ap.add_argument(
        "--symmetric-blend",
        type=int,
        default=0,
        help="Also softly pull the first/last N frames toward each other before bridge generation",
    )
    ap.add_argument(
        "--copy-audio",
        action="store_true",
        help="Try to keep input audio (usually not ideal for seamless loops)",
    )
    ap.add_argument("--crf", type=int, default=12, help="libx264 CRF (default: 12)")
    ap.add_argument("--preset", default="slow", help="libx264 preset (default: slow)")
    ap.add_argument(
        "--pix-fmt",
        default="yuv420p",
        help="Output pixel format (default: yuv420p)",
    )
    ap.add_argument(
        "--max-mb",
        type=float,
        default=1.0,
        help="Maximum output file size in MB (default: 1.0)",
    )
    summary = postprocess_clip(ap.parse_args())

    print(f"Input FPS: {summary['fps']:.6f}")
    print(f"Input frames: {summary['input_frames']}")
    print(f"Loop mode: {summary['loop_mode']}")
    print(f"Normalized frames: {summary['normalized_frames']}")
    print(f"Bridge frames: {summary['bridge_frames']}")
    print(f"Output frames: {summary['output_frames']}")
    print(f"Encode attempts: {summary['encode_attempts']}")
    print(f"Final scale: {summary['final_scale']:.4f}")
    print(f"Final size (bytes): {summary['final_size_bytes']}")
    print(f"Target max size (MB): {summary['target_max_mb']:g}")
    print(f"Wrote: {summary['output_path']}")


if __name__ == "__main__":
    main()
