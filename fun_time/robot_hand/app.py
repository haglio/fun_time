from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from collections import OrderedDict
from pathlib import Path

import tkinter as tk

from ..config import load_config
from ..logging_utils import configure_logging, enable_faulthandler, install_exception_logging
from .state import SharedState, udp_reader
from .video import decode_video_to_pil_frames, make_photo, scan_clips


def _preparse_config(argv: list[str] | None) -> str | None:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--config")
    known, _ = ap.parse_known_args(argv)
    return known.config


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Robot Hand clip player.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--clips-folder", default=str(config.paths.clips_dir))
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--beats-per-loop", type=float, default=config.robot_hand.beats_per_loop)
    ap.add_argument("--reverse", action="store_true", default=config.robot_hand.reverse)
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

    def consume_command_file():
        try:
            if not command_file.exists():
                return None
            text = command_file.read_text(encoding="utf-8").replace("\ufeff", "").strip().upper()
            if not text:
                return None
            command_file.write_text("", encoding="utf-8")
            return text
        except Exception:
            logger.exception("Failed to consume command file %s", command_file)
            return None

    clips_folder = Path(args.clips_folder)
    if not clips_folder.exists():
        raise RuntimeError(f"Clips folder does not exist: {clips_folder}")

    clips = scan_clips(clips_folder)
    clip_index = {"value": 0}

    root = tk.Tk()

    def tk_callback_exception(exc_type, exc, tb):
        logger.critical("Tk callback failed", exc_info=(exc_type, exc, tb))
        try:
            status_var.set(f"Error: {exc}\nSee {config.log_file('robot_hand_listener').name}")
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

    clip_cache: OrderedDict[Path, dict] = OrderedDict()
    current_clip_path = {"value": None}
    current_frame_index = {"value": None}

    render_queue: list[int] = []
    render_scheduled = {"value": False}
    resize_after_id = {"value": None}
    hide_status_after_id = {"value": None}
    window_visible = {"value": False}

    engine = {
        "phase": 0.0,
        "estimated_bpm": None,
        "target_bpm": None,
        "last_tick": time.monotonic(),
        "seen_sync_pulse_id": 0,
    }

    load_state = {
        "request_id": 0,
        "loading": False,
        "loaded_clip_path": None,
        "loaded_frames": None,
        "load_error": None,
    }

    notify_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    last_visible_sent = {"value": None}

    def notify_clip(path: Path):
        notify_sock.sendto(f"CLIP {path.stem}".encode("utf-8"), (args.notify_host, args.notify_port))

    def notify_visible(is_visible: bool):
        val = 1 if is_visible else 0
        if last_visible_sent["value"] != val:
            notify_sock.sendto(f"VISIBLE {val}".encode("utf-8"), (args.notify_host, args.notify_port))
            last_visible_sent["value"] = val

    def current_viewport():
        return max(1, container.winfo_width()), max(1, container.winfo_height())

    def show_status():
        status_label.place(x=10, y=10)

    def hide_status():
        if state.error is None and not load_state["loading"]:
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

    def trim_cache():
        while len(clip_cache) > args.clip_cache_size:
            oldest_key = next(iter(clip_cache))
            if oldest_key == current_clip_path["value"]:
                break
            clip_cache.popitem(last=False)

    def clip_entry_for(path: Path):
        entry = clip_cache[path]
        clip_cache.move_to_end(path)
        return entry

    def loader_thread_fn(path: Path, request_id: int):
        try:
            frames = decode_video_to_pil_frames(path)
            load_state["loaded_clip_path"] = path
            load_state["loaded_frames"] = frames
            load_state["load_error"] = None
            load_state["request_id_done"] = request_id
        except Exception as exc:
            logger.exception("Failed to decode clip %s", path)
            load_state["loaded_clip_path"] = path
            load_state["loaded_frames"] = None
            load_state["load_error"] = str(exc)
            load_state["request_id_done"] = request_id

    def request_clip_load(path: Path):
        if path in clip_cache:
            return

        load_state["request_id"] += 1
        request_id = load_state["request_id"]
        load_state["loading"] = True
        load_state["loaded_clip_path"] = None
        load_state["loaded_frames"] = None
        load_state["load_error"] = None
        load_state["request_id_done"] = None

        status_var.set(f"Loading clip...\n{path.name}")
        show_status()

        thread = threading.Thread(target=loader_thread_fn, args=(path, request_id), daemon=True, name="robot-hand-loader")
        thread.start()

    def adopt_loaded_clip_if_ready():
        request_id_done = load_state.get("request_id_done")
        if request_id_done is None:
            return

        if request_id_done != load_state["request_id"]:
            load_state["request_id_done"] = None
            load_state["loaded_clip_path"] = None
            load_state["loaded_frames"] = None
            load_state["load_error"] = None
            return

        path = load_state["loaded_clip_path"]
        err = load_state["load_error"]
        frames = load_state["loaded_frames"]

        load_state["request_id_done"] = None
        load_state["loading"] = False

        if err:
            with state.lock:
                state.error = err
            return

        clip_cache[path] = {
            "pil_frames": frames,
            "photo_frames": [None] * len(frames),
            "photo_size": None,
        }
        clip_cache.move_to_end(path)
        trim_cache()

        if current_clip_path["value"] == path:
            prepare_active_clip_for_current_size()
            schedule_hide_status()

    def set_current_clip(path: Path):
        current_clip_path["value"] = path
        current_frame_index["value"] = None
        notify_clip(path)

        if path in clip_cache:
            prepare_active_clip_for_current_size()
            schedule_hide_status()
        else:
            request_clip_load(path)

    def prepare_active_clip_for_current_size():
        path = current_clip_path["value"]
        if path is None or path not in clip_cache:
            return

        entry = clip_entry_for(path)
        size = current_viewport()
        entry["photo_size"] = size
        entry["photo_frames"] = [None] * len(entry["pil_frames"])
        render_queue.clear()

        for idx in range(len(entry["pil_frames"])):
            render_queue.append(idx)

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
            if path is None or path not in clip_cache:
                return

            entry = clip_entry_for(path)
            size = current_viewport()
            if entry["photo_size"] != size:
                return

            count = 0
            while render_queue and count < args.render_batch:
                idx = render_queue.pop(0)
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
        if path is None or path not in clip_cache:
            return None

        entry = clip_entry_for(path)
        size = current_viewport()

        if entry["photo_size"] != size:
            prepare_active_clip_for_current_size()
            entry = clip_entry_for(path)

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

    def update_engine(now, auto_active, raw_bpm, sync_pulse_id):
        dt = now - engine["last_tick"]
        engine["last_tick"] = now
        dt = max(0.0, min(dt, 0.1))

        if raw_bpm is not None:
            engine["target_bpm"] = float(raw_bpm)
            if engine["estimated_bpm"] is None:
                engine["estimated_bpm"] = float(raw_bpm)

        if engine["estimated_bpm"] is not None and engine["target_bpm"] is not None:
            alpha = max(0.0, min(1.0, args.bpm_smoothing))
            engine["estimated_bpm"] = engine["estimated_bpm"] + (engine["target_bpm"] - engine["estimated_bpm"]) * alpha

        if auto_active and engine["estimated_bpm"] and engine["estimated_bpm"] > 0:
            loop_duration = (60.0 / engine["estimated_bpm"]) * args.beats_per_loop
            engine["phase"] = (engine["phase"] + (dt / loop_duration)) % 1.0
        else:
            loop_duration = None

        if sync_pulse_id != engine["seen_sync_pulse_id"]:
            engine["seen_sync_pulse_id"] = sync_pulse_id
            phase = engine["phase"]
            error = -phase if phase <= 0.5 else (1.0 - phase)
            strength = max(0.0, min(1.0, args.sync_strength))
            engine["phase"] = (engine["phase"] + error * strength) % 1.0

        return loop_duration

    def refresh():
        try:
            now = time.monotonic()
            adopt_loaded_clip_if_ready()

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

            if visible != window_visible["value"]:
                if visible:
                    if last_visible_sent["value"] != 1 and current_clip_path["value"] is not None:
                        notify_clip(current_clip_path["value"])
                    notify_visible(True)
                    root.deiconify()
                else:
                    notify_visible(False)
                    root.withdraw()

                window_visible["value"] = visible

            if error:
                status_var.set(f"Error:\n{error}")
                show_status()
                root.after(100, refresh)
                return

            loop_duration = update_engine(now, auto_active, raw_bpm, sync_pulse_id)

            cmd = consume_command_file()
            if cmd == "PREV":
                step_clip(-1)
            elif cmd == "NEXT":
                step_clip(1)
            elif cmd == "NUDGE25":
                engine["phase"] = (engine["phase"] + 0.25) % 1.0

            path = current_clip_path["value"]
            active_entry = clip_cache.get(path) if path in clip_cache else None
            clip_name = path.name if path else "(none)"

            if active_entry and active_entry["pil_frames"]:
                frame_count = len(active_entry["pil_frames"])
                logical_index = int(engine["phase"] * frame_count)
                if logical_index >= frame_count:
                    logical_index = frame_count - 1

                display_index = (frame_count - 1) - logical_index if args.reverse else logical_index

                if not auto_active and current_frame_index["value"] is not None:
                    display_index = current_frame_index["value"]

                photo = ensure_current_frame_photo(display_index)
                if photo is not None and current_frame_index["value"] != display_index:
                    image_label.configure(image=photo)
                    image_label.image = photo
                    current_frame_index["value"] = display_index

                est_bpm_text = f"{engine['estimated_bpm']:.2f}" if engine["estimated_bpm"] is not None else "n/a"
                status_var.set(
                    f"clip={clip_name}\n"
                    f"clip_index={clip_index['value'] + 1}/{len(clips)}\n"
                    f"frame={display_index + 1}/{frame_count}\n"
                    f"visible={visible}\n"
                    f"state={'auto-on' if auto_active else 'auto-off'}\n"
                    f"phase={engine['phase']:.3f}\n"
                    f"raw_bpm={raw_bpm}\n"
                    f"est_bpm={est_bpm_text}\n"
                    f"beats={beats}\n"
                    f"loop_duration={loop_duration}\n"
                    f"stroke={stroke_name}\n"
                    f"pattern_duration={pattern_duration}\n"
                    f"loading={load_state['loading']}\n"
                    f"last_msg={last_msg}\n"
                    f"keys=[ and ] switch clips"
                )
            else:
                status_var.set(
                    f"clip={clip_name}\n"
                    f"clip_index={clip_index['value'] + 1}/{len(clips)}\n"
                    f"loading={load_state['loading']}\n"
                    f"keys=[ and ] switch clips"
                )
                show_status()

            root.after(16, refresh)
        except Exception as exc:
            logger.exception("refresh failed")
            status_var.set(f"Error: {exc}\nSee {config.log_file('robot_hand_listener').name}")
            show_status()
            root.after(250, refresh)

    def on_close():
        stop_event.set()
        notify_visible(False)
        notify_sock.close()
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