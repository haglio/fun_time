from __future__ import annotations

from dataclasses import dataclass

import tkinter as tk

from .status_text import exception_status_text


@dataclass(frozen=True)
class RobotHandView:
    root: object
    container: object
    image_label: object
    status_var: object
    status_label: object


def create_robot_hand_view(*, width: int, height: int, x: int, y: int, tk_module=tk) -> RobotHandView:
    root = tk_module.Tk()
    root.title("Robot Hand")
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.configure(bg="black")

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
