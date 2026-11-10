from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from PIL import Image

from fun_time.fun_time_funestras import FunTimeFunestra

STAND_IN_MAIN = textwrap.dedent(
    """
    import argparse
    import ctypes
    import json
    import os
    import time
    import tkinter as tk
    from ctypes import wintypes
    from pathlib import Path

    from funestra.file_channel import (
        append_command,
        consume_command_file,
        publish_whole,
    )
    from funestra.playlist import PlaylistItem, write_playlist
    from funestra.satellite_hud import HudModel, hud_text

    SIDES = ("portrait", "landscape")
    IMAGES = Path(__file__).resolve().parent.parent / "images"

    parser = argparse.ArgumentParser()
    parser.add_argument("--fun-time", action="store_true")
    parser.add_argument("--showing", choices=SIDES)
    parser.add_argument("--caption", default="Origenerator")
    for flag in ("--x", "--y", "--width", "--height"):
        parser.add_argument(flag, type=int, default=0)
    parser.add_argument("--command-file")
    parser.add_argument("--status-file")
    for side in SIDES:
        for name in ("playlist", "cmd-file", "status-file", "hud-file"):
            parser.add_argument(f"--{side}-{name}")
    args, _rest = parser.parse_known_args()

    root = tk.Tk()
    root.withdraw()  # the main window arrives only after the "boot"

    booted = False
    status_held_back_as_a_busy_app_would = False
    state_dir = Path(__file__).resolve().parent.parent / "state"
    offer = state_dir / "fun_time_offer.txt"
    showing = state_dir / "fun_time_showing.txt"
    showing_a_slideshow = args.showing
    takeover = state_dir / "fun_time_takeover.json"

    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    def pin_to_the_named_rect():
        # By the OUTER rect, the way the real app pins its frameless window: a
        # frame spilling past the rect it was named sits over a Funestra.
        user32.SetWindowPos(int(root.wm_frame(), 16), None, args.x, args.y,
                            max(args.width, 120), max(args.height, 80),
                            0x0014)  # SWP_NOZORDER | SWP_NOACTIVATE

    def park_as_hosted():
        global booted
        root.geometry(
            f"{max(args.width, 120)}x{max(args.height, 80)}+{args.x}+{args.y}")
        root.attributes("-topmost", True)
        root.deiconify()
        root.update_idletasks()
        pin_to_the_named_rect()
        root.iconify()  # boots parked, like the real app
        booted = True  # only now does the real app's bridge start publishing

    held = set()

    def handed(side, name):
        value = getattr(args, f"{side}_{name}")
        return Path(value) if value else None

    def this_process_created_at():
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4)
        times = [wintypes.FILETIME() for _ in range(4)]
        kernel32.GetProcessTimes(kernel32.GetCurrentProcess(),
                                 *[ctypes.byref(t) for t in times])
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def answer_a_takeover():
        global args, status_held_back_as_a_busy_app_would, showing_a_slideshow
        if booted or not takeover.exists():
            return
        try:
            asked = json.loads(takeover.read_text(encoding="utf-8"))
            takeover.unlink()
        except (OSError, ValueError):
            return
        if asked.get("pid") != os.getpid():
            return
        offer.unlink(missing_ok=True)
        showing.unlink(missing_ok=True)
        showing_a_slideshow = None
        args, _unused = parser.parse_known_args(asked["args"])
        status_held_back_as_a_busy_app_would = True
        park_as_hosted()

    def offer_itself():
        state_dir.mkdir(parents=True, exist_ok=True)
        offered = f"{os.getpid()} {this_process_created_at()}"
        offer.write_text(offered, encoding="utf-8")
        if showing_a_slideshow:
            showing.write_text(f"{offered} {showing_a_slideshow}", encoding="utf-8")

    def go_back_to_standalone():
        global booted
        close_slideshows()
        root.attributes("-topmost", False)
        root.geometry("640x480+40+40")
        root.deiconify()
        booted = False
        offer_itself()

    if args.fun_time:
        splash = tk.Toplevel(root)
        splash.title("Origenerator")  # the caption twin the session must survive
        splash.geometry("200x80+10+10")

        def finish_boot():
            splash.destroy()
            root.title("Origenerator")
            park_as_hosted()

        root.after(3000, finish_boot)
    else:
        # Kept through a takeover, as the real app keeps a preview's.
        root.title(args.caption)
        root.geometry("640x480+40+40")
        root.deiconify()
        offer_itself()

    def note(line):
        # In the session's own state dir, which a failed run keeps: a dropped
        # verb or a refused write shows in no other file.
        where = Path(args.status_file).parent if args.status_file else state_dir
        where.mkdir(parents=True, exist_ok=True)
        with (where / "origenerator_stub.log").open("a", encoding="utf-8") as handle:
            print(f"{time.time():.3f} {line}", file=handle)

    def slides_of(side):
        listed = IMAGES.parent / "slides.json"
        if listed.exists():
            return [Path(slide) for slide in json.loads(listed.read_text(encoding="utf-8"))[side]]
        return [IMAGES / f"{side}.png"]

    def open_slideshow(side):
        playlist = handed(side, "playlist")
        if side in held or playlist is None:
            return
        time.sleep(1.5)  # the real app's fill of a side, which takes it seconds
        publish_whole(handed(side, "hud_file"), hud_text(
            HudModel(funestra=side, title=f"Stub {side} slideshow")))
        write_playlist(playlist, [PlaylistItem(slide) for slide in slides_of(side)])
        queued = append_command(handed(side, "cmd_file"), "RELOAD_PLAYLIST")
        note(f"{side}: list written, reload queued={queued}")
        held.add(side)

    def close_slideshow(side):
        if side in held:
            publish_whole(handed(side, "hud_file"), "")
            held.discard(side)

    def close_slideshows():
        for side in list(held):
            close_slideshow(side)

    def publish_status():
        if not args.status_file or not booted or status_held_back_as_a_busy_app_would:
            return
        lines = []
        for side in SIDES:
            lines.append(f"{side}_active={'1' if side in held else '0'}")
            lines.append(f"{side}_video=")
            lines.append(f"{side}_locked=0")
        Path(args.status_file).write_text(
            "".join(f"{line}\\n" for line in lines), encoding="utf-8")

    def poll():
        answer_a_takeover()
        command_file = Path(args.command_file) if args.command_file else None
        if command_file is not None:
            # Claimed by rename, like every Funestra's: read-then-unlink destroys
            # a verb the session appends between the two, and an open slideshow lost
            # that way reads as a Funestra that never took the app's list.
            verbs = consume_command_file(command_file)
            for verb in verbs:
                note(f"told {verb}")
            if "QUIT" in verbs:
                root.destroy()
                return
            for side in SIDES:
                if f"{side}_open_slideshow".upper() in verbs:
                    open_slideshow(side)
            if "CLOSE_SLIDESHOWS" in verbs:
                close_slideshows()
            if "RELEASE" in verbs:
                go_back_to_standalone()
        publish_status()
        root.after(150, poll)

    root.after(150, poll)
    root.mainloop()
    """
)

_FUNESTRAS = (FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)
AN_INTERPRETER_WITH_TKINTER_AND_FUNESTRA = sys.executable


def write_stand_in_checkout(destination: Path, slides: dict[str, list[str]] | None = None) -> Path:
    package = destination / "origenerator"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(STAND_IN_MAIN, encoding="utf-8")
    images = destination / "images"
    images.mkdir(exist_ok=True)
    for funestra, color in zip(_FUNESTRAS, ((40, 90, 160), (160, 90, 40)), strict=True):
        Image.new("RGB", (320, 240), color).save(images / f"{funestra.label}.png")
    if slides:
        (destination / "slides.json").write_text(json.dumps(slides), encoding="utf-8")
    return destination


def host_the_stand_in(config: dict, checkout: Path) -> None:
    config["paths"]["origenerator_dir"] = str(checkout)
    config["paths"]["origenerator_python_exe"] = AN_INTERPRETER_WITH_TKINTER_AND_FUNESTRA
