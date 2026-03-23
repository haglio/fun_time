from __future__ import annotations


class StatusOverlayController:
    def __init__(self, *, root, label, hide_delay_ms: int, can_hide):
        self.root = root
        self.label = label
        self.hide_delay_ms = hide_delay_ms
        self.can_hide = can_hide
        self._hide_after_id = None

    def show(self) -> None:
        self.label.place(x=10, y=10)

    def hide(self) -> None:
        if self.can_hide():
            self.label.place_forget()

    def schedule_hide(self) -> None:
        if self._hide_after_id is not None:
            self.root.after_cancel(self._hide_after_id)
        self._hide_after_id = self.root.after(self.hide_delay_ms, self.hide)

    def on_mouse_motion(self, _event=None) -> None:
        self.show()
        self.schedule_hide()

    def on_mouse_leave(self, _event=None) -> None:
        self.hide()
