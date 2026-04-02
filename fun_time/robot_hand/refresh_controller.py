from __future__ import annotations

import time
from pathlib import Path

from ..runtime_support import consume_command_file
from .engine import update_engine
from .refresh_logic import display_index_for_phase, read_shared_state_snapshot
from .runtime_commands import apply_runtime_command, get_engine_estimated_bpm


class RobotHandRefreshController:
    def __init__(
        self,
        *,
        state,
        loader,
        notifier,
        renderer,
        selection,
        engine,
        rh_paused,
        command_file: Path,
        paused_file: Path,
        beats_per_loop: float,
        bpm_smoothing: float,
        sync_strength: float,
        show_window,
        hide_window,
        set_loading_text,
        logger,
        log_name: str,
        now_source=time.monotonic,
        consume_command=consume_command_file,
        read_paused_state=None,
    ):
        self.state = state
        self.loader = loader
        self.notifier = notifier
        self.renderer = renderer
        self.selection = selection
        self.engine = engine
        self.rh_paused = rh_paused
        self.command_file = command_file
        self.paused_file = paused_file
        self.beats_per_loop = beats_per_loop
        self.bpm_smoothing = bpm_smoothing
        self.sync_strength = sync_strength
        self.show_window = show_window
        self.hide_window = hide_window
        self.set_loading_text = set_loading_text
        self.logger = logger
        self.log_name = log_name
        self.now_source = now_source
        self.consume_command = consume_command
        self.read_paused_state = read_paused_state or (lambda _path, logger=None: False)
        self.window_visible = False

    def refresh(self) -> None:
        try:
            self._refresh_once()
        except Exception as exc:
            self.logger.exception("refresh failed")

    def _refresh_once(self) -> None:
        now = self.now_source()
        self.loader.adopt_loaded_clip_if_ready()
        self.loader.adopt_prefetch_if_ready()
        self.selection.adopt_pending_clip()

        shared = read_shared_state_snapshot(self.state)
        self.rh_paused["value"] = self.read_paused_state(self.paused_file, logger=self.logger)

        self.window_visible = self.notifier.sync_window_visibility(
            desired_visible=shared.visible,
            window_visible=self.window_visible,
            current_clip_path=self.renderer.current_clip_path,
            show_window=self.show_window,
            hide_window=self.hide_window,
        )

        if shared.error:
            return

        loop_duration = update_engine(
            self.engine,
            now=now,
            auto_active=shared.auto_active,
            raw_bpm=shared.raw_bpm,
            sync_pulse_id=shared.sync_pulse_id,
            beats_per_loop=self.beats_per_loop,
            bpm_smoothing=self.bpm_smoothing,
            sync_strength=self.sync_strength,
            paused=self.rh_paused["value"],
        )

        apply_runtime_command(
            self.consume_command(self.command_file, logger=self.logger),
            engine=self.engine,
            rh_paused=self.rh_paused,
            step_clip=self.selection.step,
        )

        active_entry = self.renderer.current_clip_entry()

        if active_entry and active_entry["frames"]:
            frame_count = len(active_entry["frames"])
            display_index = display_index_for_phase(
                phase=self.engine.phase,
                frame_count=frame_count,
                auto_active=shared.auto_active,
                current_frame_index=self.renderer.current_frame_index,
            )
            self.renderer.display_frame(display_index)

        # Show or clear the loading overlay
        pending = self.selection.pending_clip_name
        self.set_loading_text(f"Loading {pending}" if pending else None)

        self.selection.request_nearby_prefetch()
