from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tkinter as tk

from .status_text import exception_status_text

WINDOW_BOTTOM_MARGIN_PX = 8


@dataclass(frozen=True)
class RobotHandView:
    root: object
    container: object
    image_label: object
    status_var: object
    status_label: object


def apply_window_outer_bounds(*, root, width: int, height: int, x: int, y: int) -> None:
    root.geometry(f"{width}x{height}+{x}+{y}")
    update_idletasks = getattr(root, "update_idletasks", None)
    if not callable(update_idletasks):
        return

    update_idletasks()

    decoration_width = max(0, (root.winfo_rootx() - root.winfo_x()) * 2)
    decoration_height = max(0, root.winfo_rooty() - root.winfo_y())
    if decoration_width == 0 and decoration_height == 0:
        return

    fitted_width = max(1, width - decoration_width)
    fitted_height = max(1, height - decoration_height - WINDOW_BOTTOM_MARGIN_PX)
    root.geometry(f"{fitted_width}x{fitted_height}+{x}+{y}")


def create_robot_hand_view(*, width: int, height: int, x: int, y: int, icon_path: Path | None = None, tk_module=tk) -> RobotHandView:
    root = tk_module.Tk()
    root.title("Robot Hand")
    apply_window_outer_bounds(root=root, width=width, height=height, x=x, y=y)
    root.configure(bg="black")
    if icon_path is not None and icon_path.exists():
        try:
            root.iconbitmap(str(icon_path))
        except Exception:
            pass

    container = tk_module.Frame(root, bg="black")
    container.pack(fill="both", expand=True)

    image_label = tk_module.Label(container, bg="black", bd=0, highlightthickness=0)
    image_label.pack(fill="both", expand=True)

    status_var = tk_module.StringVar(value="Starting...")
    status_label = tk_module.Label(
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
    return RobotHandView(
        root=root,
        container=container,
        image_label=image_label,
        status_var=status_var,
        status_label=status_label,
    )


def install_tk_exception_handler(*, root, logger, status_setter, show_status, log_name: str) -> None:
    def tk_callback_exception(exc_type, exc, tb):
        logger.critical("Tk callback failed", exc_info=(exc_type, exc, tb))
        try:
            status_setter(exception_status_text(str(exc), log_name=log_name))
            show_status()
        except Exception:
            logger.exception("Failed to update status after Tk exception")

    root.report_callback_exception = tk_callback_exception
