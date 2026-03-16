import argparse
import re
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
import tkinter as tk

import serial
from PIL import Image, ImageTk, ImageOps

SUPPORTED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
RE_BPM = re.compile(r"\bbpm\s+(\d+),\s+beats\s+(\d+)", re.IGNORECASE)
RE_STROKE = re.compile(r"StrokeName:\s*([^,]+),\s*PatternDuration:\s*([0-9.]+)", re.IGNORECASE)


def natural_key(path: Path):
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_line = ""
        self.stroke_name = ""
        self.pattern_duration = None
        self.raw_bpm = None
        self.beats = None
        self.auto_active = False
        self.sync_pulse_id = 0
        self.error = None


def scan_clips(folder: Path):
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_VIDEO_EXTS]
    files.sort(key=natural_key)
    if not files:
        raise RuntimeError(f"No video clips found in: {folder}")
    return files


def ffprobe_size(path: Path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    if "x" not in out:
        raise RuntimeError(f"Could not get video size for: {path}")
    w, h = out.split("x", 1)
    return int(w), int(h)


def decode_video_to_pil_frames(path: Path):
    width, height = ffprobe_size(path)
    frame_size = width * height * 3

    cmd = [
        "ffmpeg",
        "-v", "error",
        "-i", str(path),
        "-map", "0:v:0",
        "-an",
        "-sn",
        "-vsync", "0",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "pipe:1",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = []

    try:
        while True:
            buf = proc.stdout.read(frame_size)
            if not buf:
                break
            if len(buf) != frame_size:
                break
            img = Image.frombytes("RGB", (width, height), buf)
            frames.append(img)
    finally:
        if proc.stdout:
            proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(stderr.strip() or f"ffmpeg failed for {path}")

    if not frames:
        raise RuntimeError(f"No frames decoded from: {path}")

    return frames


def make_photo(img: Image.Image, max_width: int, max_height: int):
    max_width = max(1, int(max_width))
    max_height = max(1, int(max_height))
    sized = ImageOps.contain(img, (max_width, max_height), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(sized)


def serial_reader(port: str, baud: int, state: SharedState, stop_event: threading.Event):
    try:
        with serial.Serial(port, baud, timeout=0.2) as ser:
            while not stop_event.is_set():
                raw = ser.readline()
                if not raw:
                    continue

                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue

                low = line.lower()

                with state.lock:
                    state.last_line = line

                    if "freemode is on" in low or "freemode tcode task started" in low:
                        state.auto_active = True
                        state.sync_pulse_id += 1

                    if "freemode is off" in low or "freemode tcode task is stopped" in low:
                        state.auto_active = False

                    m = RE_STROKE.search(line)
                    if m:
                        state.stroke_name = m.group(1).strip()
                        try:
                            state.pattern_duration = float(m.group(2))
                        except ValueError:
                            state.pattern_duration = None
                        state.sync_pulse_id += 1

                    m = RE_BPM.search(line)
                    if m:
                        state.raw_bpm = int(m.group(1))
                        state.beats = int(m.group(2))

                    if "continue strokename:" in low or "start transition" in low:
                        state.sync_pulse_id += 1

    except Exception as e:
        with state.lock:
            state.error = str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--clips-folder", default="clips")
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--beats-per-loop", type=float, default=1.0)
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--clip-cache-size", type=int, default=2)
    ap.add_argument("--render-batch", type=int, default=6)
    ap.add_argument("--bpm-smoothing", type=float, default=0.14)
    ap.add_argument("--sync-strength", type=float, default=0.35)
    args = ap.parse_args()

    clips_folder = Path(args.clips_folder)
    if not clips_folder.exists():
        raise RuntimeError(f"Clips folder does not exist: {clips_folder}")

    clips = scan_clips(clips_folder)
    clip_index = {"value": 0}

    root = tk.Tk()
    root.title("Robot Hand")
    root.geometry(f"{args.width}x{args.height}")
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

    stop_event = threading.Event()
    state = SharedState()

    serial_thread = threading.Thread(
        target=serial_reader,
        args=(args.port, args.baud, state, stop_event),
        daemon=True,
    )
    serial_thread.start()

    clip_cache = OrderedDict()
    current_clip_path = {"value": None}
    current_frame_index = {"value": None}
    current_photo = {"value": None}
    current_size = {"value": None}

    render_queue = []
    render_scheduled = {"value": False}
    resize_after_id = {"value": None}
    hide_status_after_id = {"value": None}

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
        hide_status_after_id["value"] = root.after(1200, hide_status)

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

    def ensure_clip_loaded(path: Path):
        if path in clip_cache:
            clip_cache.move_to_end(path)
            return True
        return False

    def loader_thread_fn(path: Path, request_id: int):
        try:
            frames = decode_video_to_pil_frames(path)
            load_state["loaded_clip_path"] = path
            load_state["loaded_frames"] = frames
            load_state["load_error"] = None
            load_state["request_id_done"] = request_id
        except Exception as e:
            load_state["loaded_clip_path"] = path
            load_state["loaded_frames"] = None
            load_state["load_error"] = str(e)
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

        t = threading.Thread(target=loader_thread_fn, args=(path, request_id), daemon=True)
        t.start()

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

        if ensure_clip_loaded(path):
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

        for i in range(len(entry["pil_frames"])):
            render_queue.append(i)

        if entry["pil_frames"]:
            first_idx = 0
            entry["photo_frames"][first_idx] = make_photo(entry["pil_frames"][first_idx], *size)
            image_label.configure(image=entry["photo_frames"][first_idx])
            image_label.image = entry["photo_frames"][first_idx]
            current_photo["value"] = entry["photo_frames"][first_idx]
            current_frame_index["value"] = first_idx

        schedule_render_step()

    def schedule_render_step():
        if not render_scheduled["value"]:
            render_scheduled["value"] = True
            root.after(1, render_step)

    def render_step():
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
            i = render_queue.pop(0)
            if entry["photo_frames"][i] is None:
                entry["photo_frames"][i] = make_photo(entry["pil_frames"][i], *size)
            count += 1

        idx = current_frame_index["value"]
        if idx is not None and 0 <= idx < len(entry["photo_frames"]) and entry["photo_frames"][idx] is not None:
            image_label.configure(image=entry["photo_frames"][idx])
            image_label.image = entry["photo_frames"][idx]
            current_photo["value"] = entry["photo_frames"][idx]

        if render_queue:
            schedule_render_step()

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
        resize_after_id["value"] = root.after(120, prepare_active_clip_for_current_size)

    def step_clip(delta: int):
        clip_index["value"] = (clip_index["value"] + delta) % len(clips)
        set_current_clip(clips[clip_index["value"]])
        status_var.set(f"Selected clip: {clips[clip_index['value']].name}")
        show_status()
        schedule_hide_status()

    def on_prev_clip(_event=None):
        step_clip(-1)

    def on_next_clip(_event=None):
        step_clip(1)

    def update_engine(now, auto_active, raw_bpm, beats, sync_pulse_id):
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
        now = time.monotonic()
        adopt_loaded_clip_if_ready()

        with state.lock:
            last_line = state.last_line
            stroke_name = state.stroke_name
            pattern_duration = state.pattern_duration
            raw_bpm = state.raw_bpm
            beats = state.beats
            auto_active = state.auto_active
            sync_pulse_id = state.sync_pulse_id
            error = state.error

        if error:
            status_var.set(f"Error:\n{error}")
            show_status()
            root.after(100, refresh)
            return

        loop_duration = update_engine(now, auto_active, raw_bpm, beats, sync_pulse_id)

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
                current_photo["value"] = photo
                current_frame_index["value"] = display_index

            status_var.set(
                f"clip={clip_name}\n"
                f"clip_index={clip_index['value'] + 1}/{len(clips)}\n"
                f"frame={display_index + 1}/{frame_count}\n"
                f"state={'auto-on' if auto_active else 'auto-off'}\n"
                f"phase={engine['phase']:.3f}\n"
                f"raw_bpm={raw_bpm}\n"
                f"est_bpm={(f'{engine['estimated_bpm']:.2f}' if engine['estimated_bpm'] is not None else 'n/a')}\n"
                f"beats={beats}\n"
                f"loop_duration={loop_duration}\n"
                f"stroke={stroke_name}\n"
                f"pattern_duration={pattern_duration}\n"
                f"loading={load_state['loading']}\n"
                f"last_line={last_line}\n"
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

    def on_close():
        stop_event.set()
        root.destroy()

    root.bind("<Motion>", on_mouse_motion)
    container.bind("<Motion>", on_mouse_motion)
    image_label.bind("<Motion>", on_mouse_motion)

    root.bind("<Leave>", on_mouse_leave)
    container.bind("<Leave>", on_mouse_leave)
    image_label.bind("<Leave>", on_mouse_leave)

    root.bind("<Configure>", on_resize)
    root.bind("[", on_prev_clip)
    root.bind("]", on_next_clip)

    root.protocol("WM_DELETE_WINDOW", on_close)

    set_current_clip(clips[0])
    root.after(16, refresh)
    root.mainloop()


if __name__ == "__main__":
    main()