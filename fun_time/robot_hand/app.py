from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

import tkinter as tk

from .clip_loader import ClipLoadController
from .clip_renderer import ClipRenderController
from .clip_runtime import ClipCacheStore, DecodeRequestState
from .clip_selection import ClipSelectionController
from .clip_sequence import ClipSequenceController
from .notifier import RobotHandNotifier
from .runtime_commands import apply_runtime_command, get_engine_estimated_bpm
from ..config import load_config
from ..logging_utils import configure_logging, enable_faulthandler, install_exception_logging
from ..runtime_support import consume_command_file, preparse_config_path
from .engine import PlaybackEngine, update_engine
from .refresh_logic import display_index_for_phase, read_shared_state_snapshot
from .state import SharedState, udp_reader
from .status_overlay import StatusOverlayController
from .status_text import (
    active_clip_status_text,
    exception_status_text,
    listener_error_status_text,
    loading_status_text,
)
from .video import decode_video_to_pil_frames, make_photo, scan_clips


def _preparse_config(argv: list[str] | None) -> str | None:
    return preparse_config_path(argv)


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Robot Hand clip player.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--clips-folder", default=str(config.paths.clips_dir))
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--beats-per-loop", type=float, default=config.robot_hand.beats_per_loop)
    ap.add_argument("--clip-cache-size", type=int, default=config.robot_hand.clip_cache_size)
    ap.add_argument("--render-batch", type=int, default=config.robot_hand.render_batch)
    ap.add_argument("--bpm-smoothing", type=float, default=config.robot_hand.bpm_smoothing)
    ap.add_argument("--sync-strength", type=float, default=config.robot_hand.sync_strength)
    ap.add_argument("--udp-host", default=config.robot_hand.udp_host)
    ap.add_argument("--udp-port", type=int, default=config.robot_hand.udp_port)
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--notify-host", default=config.robot_hand.notify_host)
    ap.add_argument("--notify-port", type=int, default=config.robot_hand.notify_port)
    ap.add_argument("--command-file", default=str(config.robot_hand_cmd_file))
    return ap


def main(argv: list[str] | None = None) -> int:
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.robot_hand", config.log_file("robot_hand_listener"))
    install_exception_logging(logger)
    fault_fp = enable_faulthandler(config.log_file("robot_hand_crash"))
    args = build_parser(config).parse_args(argv)

    try:
        return run_listener(args, config, logger)
    finally:
        try:
            fault_fp.close()
        except Exception:
            pass


def run_listener(args, config, logger: logging.Logger) -> int:
    command_file = Path(args.command_file)

    clips_folder = Path(args.clips_folder)
    if not clips_folder.exists():
        raise RuntimeError(f"Clips folder does not exist: {clips_folder}")

    clips = scan_clips(clips_folder, shuffle_on_load=config.robot_hand.shuffle_on_load)
    clip_sequence = ClipSequenceController(clips)

    root = tk.Tk()

    def tk_callback_exception(exc_type, exc, tb):
        logger.critical("Tk callback failed", exc_info=(exc_type, exc, tb))
        try:
            status_var.set(exception_status_text(str(exc), log_name=config.log_file("robot_hand_listener").name))
            status_overlay.show()
        except Exception:
            logger.exception("Failed to update status after Tk exception")

    root.report_callback_exception = tk_callback_exception

    root.title("Robot Hand")
    root.geometry(f"{args.width}x{args.height}+{args.x}+{args.y}")
    root.configure(bg="black")

    container = tk.Frame(root, bg="black")
    container.pack(fill="both", expand=True)

    image_label = tk.Label(container, bg="black", bd=0, highlightthickness=0)
    image_label.pack(fill="both", expand=True)

    status_var = tk.StringVar(value="Starting...")
    status_label = tk.Label(
        container,
        textvariable=status_var,
        justify="left",
        font=("Consolas", 10),
        bg="#111111",
        fg="white",
        bd=1,
        relief="solid",
        padx=8,
        pady=6,
    )

    state = SharedState()
    stop_event = threading.Event()

    udp_thread = threading.Thread(
        target=udp_reader,
        args=(args.udp_host, args.udp_port, state, stop_event, logger),
        daemon=True,
        name="robot-hand-udp",
    )
    udp_thread.start()

    clip_store = ClipCacheStore(limit=args.clip_cache_size)
    resize_after_id = {"value": None}
    window_visible = {"value": False}

    engine = PlaybackEngine(last_tick=time.monotonic())

    rh_paused = {"value": False}

    load_state = DecodeRequestState()
    prefetch_state = DecodeRequestState()
    notifier = RobotHandNotifier(args.notify_host, args.notify_port)
    status_overlay = StatusOverlayController(
        root=root,
        label=status_label,
        hide_delay_ms=config.robot_hand.status_hide_ms,
        can_hide=lambda: state.error is None and not load_state.loading,
    )

    def current_viewport():
        return max(1, container.winfo_width()), max(1, container.winfo_height())

    renderer = ClipRenderController(
        clip_store=clip_store,
        image_label=image_label,
        make_photo=make_photo,
        viewport_getter=current_viewport,
        schedule_after=root.after,
        render_batch=args.render_batch,
        logger=logger,
    )

    def start_background_job(target, path: Path, request_id: int, name: str):
        thread = threading.Thread(
            target=target,
            args=(path, request_id),
            daemon=True,
            name=name,
        )
        thread.start()

    def record_listener_error(message: str):
        with state.lock:
            state.error = message

    loader = ClipLoadController(
        clip_store=clip_store,
        load_state=load_state,
        prefetch_state=prefetch_state,
        current_clip_path_getter=lambda: renderer.current_clip_path,
        decode_clip=decode_video_to_pil_frames,
        start_background_job=start_background_job,
        logger=logger,
        on_loading_requested=lambda path: (status_var.set(f"Loading clip...\n{path.name}"), status_overlay.show()),
        on_active_clip_loaded=lambda: (renderer.prepare_active_clip_for_current_size(), status_overlay.schedule_hide()),
        on_error=record_listener_error,
    )
    selection = ClipSelectionController(
        sequence=clip_sequence,
        clip_store=clip_store,
        loader=loader,
        renderer=renderer,
        notifier=notifier,
        set_status_text=status_var.set,
        show_status=status_overlay.show,
        schedule_status_hide=status_overlay.schedule_hide,
    )

    def on_resize(_event=None):
        if resize_after_id["value"] is not None:
            root.after_cancel(resize_after_id["value"])
        resize_after_id["value"] = root.after(config.robot_hand.resize_debounce_ms, renderer.prepare_active_clip_for_current_size)

    def step_clip(delta: int):
        selection.step(delta)

    def refresh():
        try:
            now = time.monotonic()
            loader.adopt_loaded_clip_if_ready()
            loader.adopt_prefetch_if_ready()

            shared = read_shared_state_snapshot(state)

            window_visible["value"] = notifier.sync_window_visibility(
                desired_visible=shared.visible,
                window_visible=window_visible["value"],
                current_clip_path=renderer.current_clip_path,
                show_window=root.deiconify,
                hide_window=root.withdraw,
            )

            if shared.error:
                status_var.set(listener_error_status_text(shared.error))
                status_overlay.show()
                root.after(100, refresh)
                return

            loop_duration = update_engine(
                engine,
                now=now,
                auto_active=shared.auto_active,
                raw_bpm=shared.raw_bpm,
                sync_pulse_id=shared.sync_pulse_id,
                beats_per_loop=args.beats_per_loop,
                bpm_smoothing=args.bpm_smoothing,
                sync_strength=args.sync_strength,
                paused=rh_paused["value"],
            )

            apply_runtime_command(
                cmd := consume_command_file(command_file, logger=logger),
                engine=engine,
                rh_paused=rh_paused,
                step_clip=step_clip,
            )

            path = renderer.current_clip_path
            active_entry = renderer.current_clip_entry()
            clip_name = path.name if path else "(none)"

            if active_entry and active_entry["pil_frames"]:
                frame_count = len(active_entry["pil_frames"])
                display_index = display_index_for_phase(
                    phase=engine.phase,
                    frame_count=frame_count,
                    auto_active=shared.auto_active,
                    current_frame_index=renderer.current_frame_index,
                )

                renderer.display_frame(display_index)

                status_var.set(
                    active_clip_status_text(
                        clip_name=clip_name,
                        clip_index=selection.current_number,
                        clip_count=selection.count,
                        frame_index=display_index + 1,
                        frame_count=frame_count,
                        visible=shared.visible,
                        auto_active=shared.auto_active,
                        phase=engine.phase,
                        raw_bpm=shared.raw_bpm,
                        estimated_bpm=get_engine_estimated_bpm(engine),
                        beats=shared.beats,
                        loop_duration=loop_duration,
                        stroke_name=shared.stroke_name,
                        pattern_duration=shared.pattern_duration,
                        loading=load_state.loading,
                        last_msg=shared.last_msg,
                    )
                )
            else:
                status_var.set(
                    loading_status_text(
                        clip_name=clip_name,
                        clip_index=selection.current_number,
                        clip_count=selection.count,
                        loading=load_state.loading,
                    )
                )
                status_overlay.show()

            selection.request_nearby_prefetch()

            root.after(16, refresh)
        except Exception as exc:
            logger.exception("refresh failed")
            status_var.set(exception_status_text(str(exc), log_name=config.log_file("robot_hand_listener").name))
            status_overlay.show()
            root.after(250, refresh)

    def on_close():
        stop_event.set()
        notifier.notify_visible(False)
        notifier.close()
        root.destroy()

    root.bind("<Motion>", status_overlay.on_mouse_motion)
    root.bind("<Leave>", status_overlay.on_mouse_leave)
    root.bind("<Configure>", on_resize)
    root.bind("[", lambda _e: step_clip(-1))
    root.bind("]", lambda _e: step_clip(1))

    root.protocol("WM_DELETE_WINDOW", on_close)

    logger.info("Loaded %s clips from %s", selection.count, clips_folder)
    selection.set_current_clip(selection.current_path)
    root.withdraw()
    root.after(16, refresh)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
