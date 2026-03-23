from __future__ import annotations


class RobotHandLifecycleController:
    def __init__(
        self,
        *,
        root,
        renderer,
        selection,
        status_overlay,
        stop_event,
        notifier,
        resize_delay_ms: int,
    ):
        self.root = root
        self.renderer = renderer
        self.selection = selection
        self.status_overlay = status_overlay
        self.stop_event = stop_event
        self.notifier = notifier
        self.resize_delay_ms = resize_delay_ms
        self._resize_after_id = None

    def bind_root_events(self) -> None:
        self.root.bind("<Motion>", self.status_overlay.on_mouse_motion)
        self.root.bind("<Leave>", self.status_overlay.on_mouse_leave)
        self.root.bind("<Configure>", self.on_resize)
        self.root.bind("[", lambda _event: self.selection.step(-1))
        self.root.bind("]", lambda _event: self.selection.step(1))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_resize(self, _event=None) -> None:
        if self._resize_after_id is not None:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(
            self.resize_delay_ms,
            self.renderer.prepare_active_clip_for_current_size,
        )

    def on_close(self) -> None:
        self.stop_event.set()
        self.notifier.notify_visible(False)
        self.notifier.close()
        self.root.destroy()
