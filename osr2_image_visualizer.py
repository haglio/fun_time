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


def load_pil(path: Path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.load()
    return img


def make_photo(img: Image.Image, max_width: int, max_height: int):
    max_width = max(1, int(max_width))
    max_height = max(1, int(max_height))
    sized = img.copy()
    sized.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
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
    ap.add_argument("--render-batch", type=int, default=4)
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise RuntimeError(f"Folder does not exist: {folder}")

    files = scan_files(folder)
    frame_count = len(files)

    root = tk.Tk()
    root.title("OSR2 Image Visualizer")
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

    pil_images = [None] * frame_count
    photo_cache = [None] * frame_count
    cache_size = {"value": None}
    last_shown = {"value": None}

    preload_done = {"value": False}
    loaded_count = {"value": 0}

    render_queue = []
    render_scheduled = {"value": False}
    resize_after_id = {"value": None}
    hide_status_after_id = {"value": None}

    initial_index = frame_count - 1 if args.reverse else 0

    state = SharedState()
    stop_event = threading.Event()

    def current_viewport():
        w = max(1, container.winfo_width())
        h = max(1, container.winfo_height())
        return w, h

    def show_status():
        status_label.place(x=10, y=10)

    def hide_status():
        if state.error is None and preload_done["value"]:
            status_label.place_forget()

    def schedule_hide_status():
        if hide_status_after_id["value"] is not None:
            root.after_cancel(hide_status_after_id["value"])
        hide_status_after_id["value"] = root.after(1200, hide_status)

    def on_mouse_motion(_event=None):
        if preload_done["value"] and state.error is None:
            show_status()
            schedule_hide_status()

    def on_mouse_leave(_event=None):
        if preload_done["value"] and state.error is None:
            hide_status()

    def set_display_index(index: int, force=False):
        if index is None or pil_images[index] is None:
            return

        viewport = current_viewport()

        if cache_size["value"] != viewport:
            photo = make_photo(pil_images[index], *viewport)
            image_label.configure(image=photo)
            image_label.image = photo
            last_shown["value"] = index
            return

        photo = photo_cache[index]
        if photo is None:
            photo = make_photo(pil_images[index], *viewport)
            photo_cache[index] = photo

        if force or last_shown["value"] != index:
            image_label.configure(image=photo)
            image_label.image = photo
            last_shown["value"] = index

    def schedule_render_step():
        if not render_scheduled["value"]:
            render_scheduled["value"] = True
            root.after(1, render_step)

    def render_step():
        render_scheduled["value"] = False

        if cache_size["value"] is None:
            return

        count = 0
        size = cache_size["value"]

        while render_queue and count < args.render_batch:
            i = render_queue.pop(0)
            if pil_images[i] is not None:
                photo_cache[i] = make_photo(pil_images[i], *size)
                count += 1

        current_index = last_shown["value"]
        if current_index is not None and photo_cache[current_index] is not None:
            image_label.configure(image=photo_cache[current_index])
            image_label.image = photo_cache[current_index]

        if render_queue:
            schedule_render_step()

    def request_rerender():
        size = current_viewport()
        if size[0] < 2 or size[1] < 2:
            return

        cache_size["value"] = size
        for i in range(frame_count):
            photo_cache[i] = None

        render_queue.clear()
        for i in range(frame_count):
            if pil_images[i] is not None:
                render_queue.append(i)

        current_index = last_shown["value"] if last_shown["value"] is not None else initial_index
        if pil_images[current_index] is not None:
            photo_cache[current_index] = make_photo(pil_images[current_index], *size)
            image_label.configure(image=photo_cache[current_index])
            image_label.image = photo_cache[current_index]

        schedule_render_step()

    def on_resize(_event=None):
        if resize_after_id["value"] is not None:
            root.after_cancel(resize_after_id["value"])
        resize_after_id["value"] = root.after(120, request_rerender)

    def preload_step():
        count = 0
        while remaining and count < args.preload_batch:
            i = remaining.pop(0)
            pil_images[i] = load_pil(files[i])
            loaded_count["value"] += 1
            if cache_size["value"] is not None:
                render_queue.append(i)
            count += 1

        if not preload_done["value"]:
            status_var.set(f"Loading frames... {loaded_count['value']}/{frame_count}")
            show_status()

        schedule_render_step()

        if remaining:
            root.after(1, preload_step)
        else:
            preload_done["value"] = True
            status_var.set("Ready")
            schedule_hide_status()

    pil_images[initial_index] = load_pil(files[initial_index])
    loaded_count["value"] = 1

    root.update_idletasks()
    cache_size["value"] = current_viewport()
    photo_cache[initial_index] = make_photo(pil_images[initial_index], *cache_size["value"])
    image_label.configure(image=photo_cache[initial_index])
    image_label.image = photo_cache[initial_index]
    last_shown["value"] = initial_index

    remaining = [i for i in range(frame_count) if i != initial_index]

    t = threading.Thread(
        target=serial_reader,
        args=(args.port, args.baud, state, stop_event),
        daemon=True,
    )
    t.start()

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
            show_status()
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

            set_display_index(display_index)

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

    root.bind("<Motion>", on_mouse_motion)
    container.bind("<Motion>", on_mouse_motion)
    image_label.bind("<Motion>", on_mouse_motion)

    root.bind("<Leave>", on_mouse_leave)
    container.bind("<Leave>", on_mouse_leave)
    image_label.bind("<Leave>", on_mouse_leave)

    root.bind("<Configure>", on_resize)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(1, preload_step)
    root.after(16, refresh)
    root.mainloop()


if __name__ == "__main__":
    main()