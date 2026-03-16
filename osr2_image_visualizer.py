import argparse
import re
import threading
import time
from pathlib import Path
import tkinter as tk

import serial
from PIL import Image, ImageTk, ImageOps

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

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
        self.bpm = None
        self.beats = None
        self.loop_start = time.monotonic()
        self.auto_active = False
        self.error = None


def scan_files(folder: Path):
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    files.sort(key=natural_key)
    if not files:
        raise RuntimeError(f"No image files found in: {folder}")
    return files


def load_photo(path: Path, max_width: int, max_height: int):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(img)


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

                now = time.monotonic()
                low = line.lower()

                with state.lock:
                    state.last_line = line

                    if "freemode is on" in low or "freemode tcode task started" in low:
                        state.auto_active = True

                    if "freemode is off" in low or "freemode tcode task is stopped" in low:
                        state.auto_active = False

                    m = RE_STROKE.search(line)
                    if m:
                        state.stroke_name = m.group(1).strip()
                        try:
                            state.pattern_duration = float(m.group(2))
                        except ValueError:
                            state.pattern_duration = None

                    m = RE_BPM.search(line)
                    if m:
                        state.bpm = int(m.group(1))
                        state.beats = int(m.group(2))
                        state.loop_start = now

    except Exception as e:
        with state.lock:
            state.error = str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--folder", default="frames")
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--beats-per-loop", type=float, default=1.0)
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--preload-batch", type=int, default=2)
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise RuntimeError(f"Folder does not exist: {folder}")

    files = scan_files(folder)
    frame_count = len(files)

    root = tk.Tk()
    root.title("OSR2 Image Visualizer")

    image_label = tk.Label(root)
    image_label.pack(padx=10, pady=10)

    status_var = tk.StringVar(value="Starting...")
    status_label = tk.Label(root, textvariable=status_var, justify="left", font=("Consolas", 10))
    status_label.pack(padx=10, pady=(0, 10), anchor="w")

    photos = [None] * frame_count
    preload_done = {"value": False}
    last_shown = {"value": None}

    initial_index = frame_count - 1 if args.reverse else 0
    photos[initial_index] = load_photo(files[initial_index], args.width, args.height)
    image_label.configure(image=photos[initial_index])
    image_label.image = photos[initial_index]
    last_shown["value"] = initial_index

    remaining = [i for i in range(frame_count) if i != initial_index]

    state = SharedState()
    stop_event = threading.Event()

    t = threading.Thread(
        target=serial_reader,
        args=(args.port, args.baud, state, stop_event),
        daemon=True,
    )
    t.start()

    def preload_step():
        count = 0
        while remaining and count < args.preload_batch:
            i = remaining.pop(0)
            photos[i] = load_photo(files[i], args.width, args.height)
            count += 1

        loaded = frame_count - len(remaining)
        status_var.set(f"Loading frames... {loaded}/{frame_count}")

        if remaining:
            root.after(1, preload_step)
        else:
            preload_done["value"] = True

    def refresh():
        now = time.monotonic()

        with state.lock:
            last_line = state.last_line
            stroke_name = state.stroke_name
            pattern_duration = state.pattern_duration
            bpm = state.bpm
            beats = state.beats
            loop_start = state.loop_start
            auto_active = state.auto_active
            error = state.error

        if error:
            status_var.set(f"Serial error: {error}")
            root.after(100, refresh)
            return

        if preload_done["value"]:
            if auto_active and bpm and bpm > 0:
                loop_duration = (60.0 / bpm) * args.beats_per_loop
                phase = ((now - loop_start) / loop_duration) % 1.0
                logical_index = int(phase * frame_count)
                if logical_index >= frame_count:
                    logical_index = frame_count - 1

                if args.reverse:
                    display_index = (frame_count - 1) - logical_index
                else:
                    display_index = logical_index
            else:
                loop_duration = None
                phase = None
                display_index = last_shown["value"] if last_shown["value"] is not None else initial_index

            if last_shown["value"] != display_index:
                photo = photos[display_index]
                image_label.configure(image=photo)
                image_label.image = photo
                last_shown["value"] = display_index

            status_var.set(
                f"frame={display_index + 1}/{frame_count}  file={files[display_index].name}\n"
                f"state={'auto-on' if auto_active else 'auto-off'}\n"
                f"phase={(f'{phase:.3f}' if phase is not None else 'frozen')}\n"
                f"bpm={bpm}  beats={beats}\n"
                f"loop_duration={loop_duration}\n"
                f"beats_per_loop={args.beats_per_loop}\n"
                f"reverse={args.reverse}\n"
                f"stroke={stroke_name}\n"
                f"pattern_duration={pattern_duration}\n"
                f"last_line={last_line}"
            )

        root.after(16, refresh)

    def on_close():
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(1, preload_step)
    root.after(16, refresh)
    root.mainloop()


if __name__ == "__main__":
    main()
