from __future__ import annotations

from PyQt6.QtWidgets import QWidget
from shared_ui.preview_icon import app_icon
from shared_ui.reference import ReferenceDialog as _SharedReferenceDialog

from fun_time import preview_marker
from fun_time.command_reference import build_reference_sections
from fun_time.project_paths import PROJECT_ICON
from fun_time.win32 import keep_in_topmost_band


class ReferenceDialog(_SharedReferenceDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(build_reference_sections(),
                         icon=app_icon(PROJECT_ICON, preview_marker.shown_as()), parent=parent)

    def sync_topmost(self, omni_paused: bool) -> None:
        keep_in_topmost_band(int(self.winId()), topmost=not omni_paused)
