from __future__ import annotations

import time
from pathlib import Path

from ..runtime_support import consume_command_file
from .engine import update_engine
from .refresh_logic import display_index_for_phase, read_shared_state_snapshot
from .runtime_commands import apply_runtime_command, get_engine_estimated_bpm
from .status_text import (
    active_clip_status_text,
    exception_status_text,
    listener_error_status_text,
    loading_status_text,
)


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
        set_status_text,
        show_status,
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
        self.set_status_text = set_status_text
        self.show_status = show_status
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
            self.set_status_text(exception_status_text(str(exc), log_name=self.log_name))
            self.show_status()

    def _refresh_once(self) -> None:
        now = self.now_source()
        self.loader.adopt_loaded_clip_if_ready()
        self.loader.adopt_prefetch_if_ready()

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
            self.set_status_text(listener_error_status_text(shared.error))
            self.show_status()
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

        path = self.renderer.current_clip_path
        active_entry = self.renderer.current_clip_entry()
        clip_name = path.name if path else "(none)"

        if active_entry and active_entry["frames"]:
            frame_count = len(active_entry["frames"])
            display_index = display_index_for_phase(
                phase=self.engine.phase,
                frame_count=frame_count,
                auto_active=shared.auto_active,
                current_frame_index=self.renderer.current_frame_index,
            )

            self.renderer.display_frame(display_index)

            self.set_status_text(
                active_clip_status_text(
                    clip_name=clip_name,
                    clip_index=self.selection.current_number,
                    clip_count=self.selection.count,
                    frame_index=display_index + 1,
                    frame_count=frame_count,
                    visible=shared.visible,
                    auto_active=shared.auto_active,
                    phase=self.engine.phase,
                    raw_bpm=shared.raw_bpm,
                    estimated_bpm=get_engine_estimated_bpm(self.engine),
                    beats=shared.beats,
                    loop_duration=loop_duration,
                    stroke_name=shared.stroke_name,
                    pattern_duration=shared.pattern_duration,
                    loading=self.loader.load_state.loading,
                    last_msg=shared.last_msg,
                )
            )
        else:
            self.set_status_text(
                loading_status_text(
                    clip_name=clip_name,
                    clip_index=self.selection.current_number,
                    clip_count=self.selection.count,
                    loading=self.loader.load_state.loading,
                )
            )
            self.show_status()

        self.selection.request_nearby_prefetch()
