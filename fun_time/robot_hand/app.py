from __future__ import annotations

import argparse
import logging
import threading
import time
from collections import deque
from pathlib import Path

import tkinter as tk

from .cache_utils import render_queue_for_frame_count
from .clip_runtime import ClipCacheStore, DecodeRequestState
from .notifier import RobotHandNotifier
from ..config import load_config
from ..logging_utils import configure_logging, enable_faulthandler, install_exception_logging
from ..runtime_support import consume_command_file, preparse_config_path
from .engine import PlaybackEngine, update_engine
from .state import SharedState, udp_reader
from .status_text import (
    active_clip_status_text,
    exception_status_text,
    listener_error_status_text,
    loading_status_text,
)
from .video import decode_video_to_pil_frames, make_photo, scan_clips


QUARTER_CYCLE_OFFSET_COMMAND = "OFFSET_QUARTER_CYCLE"
LEGACY_QUARTER_CYCLE_OFFSET_COMMAND = "NUDGE25"


def _get_engine_phase(engine) -> float:
    if isinstance(engine, dict):
        return float(engine["phase"])
    return float(engine.phase)


def _set_engine_phase(engine, value: float) -> None:
    if isinstance(engine, dict):
        engine["phase"] = value
    else:
        engine.phase = value


def _get_engine_estimated_bpm(engine) -> float | None:
    if isinstance(engine, dict):
        value = engine.get("estimated_bpm")
    else:
        value = engine.estimated_bpm
    return None if value is None else float(value)


def apply_runtime_command(command, *, engine, rh_paused, step_clip) -> bool:
    if not command:
        return False

    normalized = command.strip().upper()
    if normalized == "PREV":
        step_clip(-1)
    elif normalized == "NEXT":
        step_clip(1)
    elif normalized in {QUARTER_CYCLE_OFFSET_COMMAND, LEGACY_QUARTER_CYCLE_OFFSET_COMMAND}:
        _set_engine_phase(engine, (_get_engine_phase(engine) + 0.25) % 1.0)
    elif normalized == "PAUSE":
        rh_paused["value"] = True
    elif normalized == "RESUME":
        rh_paused["value"] = False
    else:
        return False
    return True


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
    clip_index = {"value": 0}

    root = tk.Tk()

    def tk_callback_exception(exc_type, exc, tb):
        logger.critical("Tk callback failed", exc_info=(exc_type, exc, tb))
        try:
            status_var.set(exception_status_text(str(exc), log_name=config.log_file("robot_hand_listener").name))
            show_status()
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
    current_clip_path = {"value": None}
    current_frame_index = {"value": None}

    render_queue: deque[int] = deque()
    render_scheduled = {"value": False}
    resize_after_id = {"value": None}
    hide_status_after_id = {"value": None}
    window_visible = {"value": False}

    engine = PlaybackEngine(last_tick=time.monotonic())

    rh_paused = {"value": False}

    load_state = DecodeRequestState()
    prefetch_state = DecodeRequestState()
    notifier = RobotHandNotifier(args.notify_host, args.notify_port)

    def current_viewport():
        return max(1, container.winfo_width()), max(1, container.winfo_height())

    def show_status():
        status_label.place(x=10, y=10)

    def hide_status():
        if state.error is None and not load_state.loading:
            status_label.place_forget()

    def schedule_hide_status():
        if hide_status_after_id["value"] is not None:
            root.after_cancel(hide_status_after_id["value"])
        hide_status_after_id["value"] = root.after(config.robot_hand.status_hide_ms, hide_status)

    def on_mouse_motion(_event=None):
        show_status()
        schedule_hide_status()

    def on_mouse_leave(_event=None):
        hide_status()

    def _cache_decoded_frames(path: Path, frames: list):
        clip_store.cache_decoded_frames(
            path,
            frames,
            protected_paths={current_clip_path["value"]},
        )

    def _adopt_decoded_frames(path: Path):
        return clip_store.adopt_decoded_frames(
            path,
            protected_paths={current_clip_path["value"]},
        )

    def loader_thread_fn(path: Path, request_id: int):
        try:
            frames = decode_video_to_pil_frames(path)
            load_state.record_success(path, frames, request_id)
        except Exception as exc:
            logger.exception("Failed to decode clip %s", path)
            load_state.record_error(path, str(exc), request_id)

    def prefetch_thread_fn(path: Path, request_id: int):
        try:
            frames = decode_video_to_pil_frames(path)
            prefetch_state.record_success(path, frames, request_id)
        except Exception as exc:
            logger.warning("Prefetch decode failed for %s: %s", path, exc)
            prefetch_state.record_error(path, str(exc), request_id)

    def request_clip_load(path: Path):
        if path in clip_store.clip_cache:
            return

        if _adopt_decoded_frames(path):
            return

        request_id = load_state.begin()

        status_var.set(f"Loading clip...\n{path.name}")
        show_status()

        thread = threading.Thread(target=loader_thread_fn, args=(path, request_id), daemon=True, name="robot-hand-loader")
        thread.start()

    def request_prefetch(path: Path):
        if path in clip_store.clip_cache or path in clip_store.decoded_frame_cache:
            return
        if load_state.loading or prefetch_state.loading:
            return

        request_id = prefetch_state.begin()

        thread = threading.Thread(
            target=prefetch_thread_fn,
            args=(path, request_id),
            daemon=True,
            name="robot-hand-prefetch",
        )
        thread.start()

    def adopt_loaded_clip_if_ready():
        result = load_state.take_completed_result()
        if result is None:
            return

        path, frames, err = result

        if err:
            with state.lock:
                state.error = err
            return

        _cache_decoded_frames(path, frames)
        _adopt_decoded_frames(path)

        if current_clip_path["value"] == path:
            prepare_active_clip_for_current_size()
            schedule_hide_status()

    def adopt_prefetch_if_ready():
        result = prefetch_state.take_completed_result()
        if result is None:
            return

        path, frames, err = result

        if err:
            return

        _cache_decoded_frames(path, frames)

    def request_nearby_prefetch():
        if len(clips) <= 1:
            return
        if load_state.loading or prefetch_state.loading:
            return

        current_index = clip_index["value"]
        for delta in (1, -1):
            candidate = clips[(current_index + delta) % len(clips)]
            if candidate not in clip_store.clip_cache and candidate not in clip_store.decoded_frame_cache:
                request_prefetch(candidate)
                return

    def set_current_clip(path: Path):
        current_clip_path["value"] = path
        current_frame_index["value"] = None
        notifier.notify_clip(path)

        if path in clip_store.clip_cache:
            prepare_active_clip_for_current_size()
            schedule_hide_status()
        else:
            request_clip_load(path)
            if path in clip_store.clip_cache:
                prepare_active_clip_for_current_size()
                schedule_hide_status()

    def prepare_active_clip_for_current_size():
        path = current_clip_path["value"]
        if path is None or path not in clip_store.clip_cache:
            return

        entry = clip_store.clip_entry_for(path)
        size = current_viewport()
        entry["photo_size"] = size
        entry["photo_frames"] = [None] * len(entry["pil_frames"])
        render_queue.clear()
        render_queue.extend(render_queue_for_frame_count(len(entry["pil_frames"])))

        if entry["pil_frames"]:
            first_idx = 0
            entry["photo_frames"][first_idx] = make_photo(entry["pil_frames"][first_idx], *size)
            image_label.configure(image=entry["photo_frames"][first_idx])
            image_label.image = entry["photo_frames"][first_idx]
            current_frame_index["value"] = first_idx

        schedule_render_step()

    def schedule_render_step():
        if not render_scheduled["value"]:
            render_scheduled["value"] = True
            root.after(1, render_step)

    def render_step():
        try:
            render_scheduled["value"] = False

            path = current_clip_path["value"]
            if path is None or path not in clip_store.clip_cache:
                return

            entry = clip_store.clip_entry_for(path)
            size = current_viewport()
            if entry["photo_size"] != size:
                return

            count = 0
            while render_queue and count < args.render_batch:
                idx = render_queue.popleft()
                if entry["photo_frames"][idx] is None:
                    entry["photo_frames"][idx] = make_photo(entry["pil_frames"][idx], *size)
                count += 1

            idx = current_frame_index["value"]
            if idx is not None and 0 <= idx < len(entry["photo_frames"]) and entry["photo_frames"][idx] is not None:
                image_label.configure(image=entry["photo_frames"][idx])
                image_label.image = entry["photo_frames"][idx]

            if render_queue:
                schedule_render_step()
        except Exception:
            logger.exception("render_step failed")

    def ensure_current_frame_photo(index: int):
        path = current_clip_path["value"]
        if path is None or path not in clip_store.clip_cache:
            return None

        entry = clip_store.clip_entry_for(path)
        size = current_viewport()

        if entry["photo_size"] != size:
            prepare_active_clip_for_current_size()
            entry = clip_store.clip_entry_for(path)

        if entry["photo_frames"][index] is None:
            entry["photo_frames"][index] = make_photo(entry["pil_frames"][index], *size)

        return entry["photo_frames"][index]

    def on_resize(_event=None):
        if resize_after_id["value"] is not None:
            root.after_cancel(resize_after_id["value"])
        resize_after_id["value"] = root.after(config.robot_hand.resize_debounce_ms, prepare_active_clip_for_current_size)

    def step_clip(delta: int):
        clip_index["value"] = (clip_index["value"] + delta) % len(clips)
        set_current_clip(clips[clip_index["value"]])
        status_var.set(f"Selected clip: {clips[clip_index['value']].name}")
        show_status()
        schedule_hide_status()

    def refresh():
        try:
            now = time.monotonic()
            adopt_loaded_clip_if_ready()
            adopt_prefetch_if_ready()

            with state.lock:
                auto_active = state.auto_active
                visible = state.visible
                raw_bpm = state.raw_bpm
                beats = state.beats
                stroke_name = state.stroke_name
                pattern_duration = state.pattern_duration
                sync_pulse_id = state.sync_pulse_id
                last_msg = state.last_msg
                error = state.error

            window_visible["value"] = notifier.sync_window_visibility(
                desired_visible=visible,
                window_visible=window_visible["value"],
                current_clip_path=current_clip_path["value"],
                show_window=root.deiconify,
                hide_window=root.withdraw,
            )

            if error:
                status_var.set(listener_error_status_text(error))
                show_status()
                root.after(100, refresh)
                return

            loop_duration = update_engine(
                engine,
                now=now,
                auto_active=auto_active,
                raw_bpm=raw_bpm,
                sync_pulse_id=sync_pulse_id,
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

            path = current_clip_path["value"]
            active_entry = clip_store.clip_cache.get(path) if path in clip_store.clip_cache else None
            clip_name = path.name if path else "(none)"

            if active_entry and active_entry["pil_frames"]:
                frame_count = len(active_entry["pil_frames"])
                logical_index = int(engine.phase * frame_count)
                if logical_index >= frame_count:
                    logical_index = frame_count - 1

                display_index = (frame_count - 1) - logical_index

                if not auto_active and current_frame_index["value"] is not None:
                    display_index = current_frame_index["value"]

                photo = ensure_current_frame_photo(display_index)
                if photo is not None and current_frame_index["value"] != display_index:
                    image_label.configure(image=photo)
                    image_label.image = photo
                    current_frame_index["value"] = display_index

                status_var.set(
                    active_clip_status_text(
                        clip_name=clip_name,
                        clip_index=clip_index["value"] + 1,
                        clip_count=len(clips),
                        frame_index=display_index + 1,
                        frame_count=frame_count,
                        visible=visible,
                        auto_active=auto_active,
                        phase=engine.phase,
                        raw_bpm=raw_bpm,
                        estimated_bpm=_get_engine_estimated_bpm(engine),
                        beats=beats,
                        loop_duration=loop_duration,
                        stroke_name=stroke_name,
                        pattern_duration=pattern_duration,
                        loading=load_state.loading,
                        last_msg=last_msg,
                    )
                )
            else:
                status_var.set(
                    loading_status_text(
                        clip_name=clip_name,
                        clip_index=clip_index["value"] + 1,
                        clip_count=len(clips),
                        loading=load_state.loading,
                    )
                )
                show_status()

            request_nearby_prefetch()

            root.after(16, refresh)
        except Exception as exc:
            logger.exception("refresh failed")
            status_var.set(exception_status_text(str(exc), log_name=config.log_file("robot_hand_listener").name))
            show_status()
            root.after(250, refresh)

    def on_close():
        stop_event.set()
        notifier.notify_visible(False)
        notifier.close()
        root.destroy()

    root.bind("<Motion>", on_mouse_motion)
    root.bind("<Leave>", on_mouse_leave)
    root.bind("<Configure>", on_resize)
    root.bind("[", lambda _e: step_clip(-1))
    root.bind("]", lambda _e: step_clip(1))

    root.protocol("WM_DELETE_WINDOW", on_close)

    logger.info("Loaded %s clips from %s", len(clips), clips_folder)
    set_current_clip(clips[0])
    root.withdraw()
    root.after(16, refresh)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
