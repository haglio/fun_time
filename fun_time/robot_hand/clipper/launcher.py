from __future__ import annotations

from pathlib import Path
from typing import Any, cast

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception:
    tk = None
    filedialog = None
    messagebox = None

from .loop_modes import LOOP_MODE_LABELS
from .paths import LAST_SESSION_FILE
from .utils import parse_timestamp, sanitize_name
from .vlc_prefill import detect_vlc_session_prefill
from .window_icons import set_tk_window_icon

APP_DISPLAY_NAME = "Clipper"
LAUNCHER_PREFILL_FALLBACK_NOTE = "If VLC is open, Clipper will try to prefill this section."


def default_launcher_mode(*, has_vlc_prefill: bool, has_last_session: bool) -> str:
    return "new" if has_vlc_prefill or not has_last_session else "load"


def prefill_note_text(note: str | None) -> str:
    return note or LAUNCHER_PREFILL_FALLBACK_NOTE


def launcher_dialog() -> dict[str, Any]:
    if tk is None or filedialog is None or messagebox is None:
        raise RuntimeError("tkinter is required for the launcher on this system")
    dialog = cast(Any, filedialog)
    msgbox = cast(Any, messagebox)
    root = tk.Tk()
    set_tk_window_icon(root)
    root.title(f"{APP_DISPLAY_NAME} Launcher")
    root.geometry("1040x560")
    root.resizable(False, False)

    vlc_prefill = detect_vlc_session_prefill()
    mode = tk.StringVar(
        value=default_launcher_mode(
            has_vlc_prefill=vlc_prefill is not None,
            has_last_session=LAST_SESSION_FILE.exists(),
        )
    )
    last_session = LAST_SESSION_FILE.read_text(encoding="utf-8").strip() if LAST_SESSION_FILE.exists() else ""
    session_json = tk.StringVar(value=last_session)
    session_name = tk.StringVar(value=vlc_prefill.session_name if vlc_prefill else "")
    video_file = tk.StringVar(value=vlc_prefill.video_file if vlc_prefill else "")
    timestamp = tk.StringVar(value=vlc_prefill.timestamp if vlc_prefill else "00:00:00")
    seconds = tk.StringVar(value="5")
    loop_mode = tk.StringVar(value="base-tip-base")
    prefill_note = tk.StringVar(value=prefill_note_text(vlc_prefill.note if vlc_prefill else None))
    result: dict[str, Any] = {"ok": False}

    def browse_json() -> None:
        p = dialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if p:
            session_json.set(p)
            mode.set("load")

    def browse_video() -> None:
        p = dialog.askopenfilename(filetypes=[("Video files", "*.mp4 *.mkv *.mov *.avi *.webm"), ("All files", "*.*")])
        if p:
            video_file.set(p)
            mode.set("new")

    def open_it(event: Any = None) -> None:
        try:
            if mode.get() == "load":
                p = Path(session_json.get().strip())
                if not p.is_file():
                    raise ValueError("Choose a valid session JSON")
                result.update({"ok": True, "mode": "load", "session_json": str(p)})
            else:
                name = sanitize_name(session_name.get())
                if not name:
                    raise ValueError("Enter a session name")
                vf = Path(video_file.get().strip())
                if not vf.is_file():
                    raise ValueError("Choose a valid video file")
                sec = float(seconds.get())
                if sec <= 0:
                    raise ValueError("Seconds must be > 0")
                parse_timestamp(timestamp.get())
                result.update(
                    {
                        "ok": True,
                        "mode": "new",
                        "session_name": name,
                        "video_file": str(vf),
                        "timestamp": timestamp.get().strip(),
                        "seconds": sec,
                        "loop_mode": loop_mode.get(),
                    }
                )
            root.destroy()
        except Exception as exc:
            msgbox.showerror(APP_DISPLAY_NAME, f"ERROR: {exc}")

    def cancel(event: Any = None) -> None:
        root.destroy()

    padx = 16
    tk.Label(root, text="Open an existing session or start a new one.", font=("Segoe UI", 14)).pack(anchor="w", padx=padx, pady=(18, 10))

    frame1 = tk.Frame(root)
    frame1.pack(fill="x", padx=padx)
    tk.Radiobutton(frame1, text="Load previous session JSON", variable=mode, value="load", font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w", pady=(0, 8))
    tk.Entry(frame1, textvariable=session_json, width=84).grid(row=1, column=0, sticky="we", padx=(28, 8), pady=(0, 8))
    tk.Button(frame1, text="Browse...", command=browse_json, width=12).grid(row=1, column=1, sticky="e", pady=(0, 8))
    frame1.grid_columnconfigure(0, weight=1)

    sep = tk.Frame(root, height=1, bg="#bbbbbb")
    sep.pack(fill="x", padx=padx, pady=8)

    frame2 = tk.Frame(root)
    frame2.pack(fill="x", padx=padx)
    tk.Radiobutton(frame2, text="Create new session", variable=mode, value="new", font=("Segoe UI", 11)).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
    tk.Label(frame2, text="Session name", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=session_name, width=56).grid(row=1, column=1, columnspan=2, sticky="w", pady=6)
    tk.Label(frame2, text="Video file", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=video_file, width=84).grid(row=2, column=1, sticky="we", pady=6, padx=(0, 8))
    tk.Button(frame2, text="Browse...", command=browse_video, width=12).grid(row=2, column=2, sticky="e", pady=6)
    tk.Label(frame2, text="Timestamp (hh:mm:ss)", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=timestamp, width=20).grid(row=3, column=1, sticky="w", pady=6)
    tk.Label(frame2, text="Seconds", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=seconds, width=10).grid(row=4, column=1, sticky="w", pady=6)
    tk.Label(frame2, text="Loop mode", font=("Segoe UI", 10)).grid(row=5, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.OptionMenu(frame2, loop_mode, *LOOP_MODE_LABELS.keys()).grid(row=5, column=1, sticky="w", pady=6)
    tk.Label(frame2, textvariable=prefill_note, font=("Segoe UI", 9), fg="#4a6580", anchor="w").grid(
        row=6, column=0, columnspan=3, sticky="w", padx=(28, 0), pady=(8, 0)
    )
    frame2.grid_columnconfigure(1, weight=1)

    session_json.trace_add("write", lambda *_: mode.set("load"))
    session_name.trace_add("write", lambda *_: mode.set("new"))
    video_file.trace_add("write", lambda *_: mode.set("new"))
    timestamp.trace_add("write", lambda *_: mode.set("new"))
    seconds.trace_add("write", lambda *_: mode.set("new"))

    bottom = tk.Frame(root)
    bottom.pack(side="bottom", fill="x", padx=padx, pady=18)
    open_btn = tk.Button(bottom, text="Open", command=open_it, width=12, default="active")
    open_btn.pack(side="right", padx=(8, 0))
    tk.Button(bottom, text="Cancel", command=cancel, width=12).pack(side="right")

    root.bind("<Return>", open_it)
    root.bind("<Escape>", cancel)
    open_btn.focus_set()
    root.mainloop()
    return result
