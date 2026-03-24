from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

from .clip_loader import ClipLoadController
from .clip_renderer import ClipRenderController
from .clip_runtime import ClipCacheStore, DecodeRequestState
from .clip_selection import ClipSelectionController
from .clip_sequence import ClipSequenceController
from .lifecycle import RobotHandLifecycleController
from .notifier import RobotHandNotifier
from .refresh_controller import RobotHandRefreshController
from .view import create_robot_hand_view, install_tk_exception_handler
from ..config import load_config
from ..logging_utils import configure_logging, enable_faulthandler, install_exception_logging
from ..runtime_support import preparse_config_path
from ..threading_utils import start_daemon_thread
from .engine import PlaybackEngine
from .state import SharedState, udp_reader
from .status_overlay import StatusOverlayController
from .video import decode_video_to_pil_frames, make_photo, scan_clips


def _preparse_config(argv: list[str] | None) -> str | None:
    return preparse_config_path(argv)


def build_parser(config) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Robot Hand clip player.")
    ap.add_argument("--config", help="Path to a JSON config file.")
    ap.add_argument("--clips-folder", default=str(config.paths.clips_dir))
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--beats-per-loop", type=float, default=config.robot_hand.beats_per_loop)
    ap.add_argument("--clip-cache-size", type=int, default=config.robot_hand.clip_cache_size)
    ap.add_argument("--render-batch", type=int, default=config.robot_hand.render_batch)
    ap.add_argument("--bpm-smoothing", type=float, default=config.robot_hand.bpm_smoothing)
    ap.add_argument("--sync-strength", type=float, default=config.robot_hand.sync_strength)
    ap.add_argument("--udp-host", default=config.robot_hand.udp_host)
    ap.add_argument("--udp-port", type=int, default=config.robot_hand.udp_port)
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--notify-host", default=config.robot_hand.notify_host)
    ap.add_argument("--notify-port", type=int, default=config.robot_hand.notify_port)
    ap.add_argument("--command-file", default=str(config.robot_hand_cmd_file))
    ap.add_argument("--paused-file", default=str(config.robot_hand_paused_file))
    return ap


def read_paused_state(path: Path, *, logger: logging.Logger | None = None) -> bool:
    try:
        if not path.exists():
            return False
        return path.read_text(encoding="utf-8").replace("\ufeff", "").strip() == "1"
    except Exception:
        if logger is not None:
            logger.exception("Failed to read Robot Hand paused state file %s", path)
        return False


def main(argv: list[str] | None = None) -> int:
    config = load_config(_preparse_config(argv))
    logger = configure_logging("fun_time.robot_hand", config.log_file("robot_hand_listener"))
    install_exception_logging(logger)
    fault_fp = enable_faulthandler(config.log_file("robot_hand_crash"))
    args = build_parser(config).parse_args(argv)

    try:
        return run_listener(args, config, logger)
    finally:
        try:
            fault_fp.close()
        except Exception:
            pass


def run_listener(args, config, logger: logging.Logger) -> int:
    command_file = Path(args.command_file)
    paused_file = Path(args.paused_file)

    clips_folder = Path(args.clips_folder)
    if not clips_folder.exists():
        raise RuntimeError(f"Clips folder does not exist: {clips_folder}")

    clips = scan_clips(clips_folder, shuffle_on_load=config.robot_hand.shuffle_on_load)
    clip_sequence = ClipSequenceController(clips)

    view = create_robot_hand_view(
        width=args.width,
        height=args.height,
        x=args.x,
        y=args.y,
        icon_path=config.project_dir / "icon.ico",
    )

    state = SharedState()
    stop_event = threading.Event()

    start_daemon_thread(
        target=udp_reader,
        args=(args.udp_host, args.udp_port, state, stop_event, logger),
        name="robot-hand-udp",
    )

    clip_store = ClipCacheStore(limit=args.clip_cache_size)

    engine = PlaybackEngine(last_tick=time.monotonic())

    rh_paused = {"value": False}

    load_state = DecodeRequestState()
    prefetch_state = DecodeRequestState()
    notifier = RobotHandNotifier(args.notify_host, args.notify_port)
    status_overlay = StatusOverlayController(
        root=view.root,
        label=view.status_label,
        hide_delay_ms=config.robot_hand.status_hide_ms,
        can_hide=lambda: state.error is None and not load_state.loading,
    )
    install_tk_exception_handler(
        root=view.root,
        logger=logger,
        status_setter=view.status_var.set,
        show_status=status_overlay.show,
        log_name=config.log_file("robot_hand_listener").name,
    )

    def current_viewport():
        return max(1, view.container.winfo_width()), max(1, view.container.winfo_height())

    renderer = ClipRenderController(
        clip_store=clip_store,
        image_label=view.image_label,
        make_photo=make_photo,
        viewport_getter=current_viewport,
        schedule_after=view.root.after,
        render_batch=args.render_batch,
        logger=logger,
    )

    def record_listener_error(message: str):
        with state.lock:
            state.error = message

    loader = ClipLoadController(
        clip_store=clip_store,
        load_state=load_state,
        prefetch_state=prefetch_state,
        current_clip_path_getter=lambda: renderer.current_clip_path,
        decode_clip=decode_video_to_pil_frames,
        start_thread=start_daemon_thread,
        logger=logger,
        on_loading_requested=lambda path: (view.status_var.set(f"Loading clip...\n{path.name}"), status_overlay.show()),
        on_active_clip_loaded=lambda: (renderer.prepare_active_clip_for_current_size(), status_overlay.schedule_hide()),
        on_error=record_listener_error,
    )
    selection = ClipSelectionController(
        sequence=clip_sequence,
        clip_store=clip_store,
        loader=loader,
        renderer=renderer,
        notifier=notifier,
        set_status_text=view.status_var.set,
        show_status=status_overlay.show,
        schedule_status_hide=status_overlay.schedule_hide,
    )

    refresh_controller = RobotHandRefreshController(
        state=state,
        loader=loader,
        notifier=notifier,
        renderer=renderer,
        selection=selection,
        engine=engine,
        rh_paused=rh_paused,
        command_file=command_file,
        paused_file=paused_file,
        beats_per_loop=args.beats_per_loop,
        bpm_smoothing=args.bpm_smoothing,
        sync_strength=args.sync_strength,
        schedule_after=view.root.after,
        show_window=view.root.deiconify,
        hide_window=view.root.withdraw,
        set_status_text=view.status_var.set,
        show_status=status_overlay.show,
        logger=logger,
        log_name=config.log_file("robot_hand_listener").name,
        read_paused_state=read_paused_state,
    )
    lifecycle = RobotHandLifecycleController(
        root=view.root,
        renderer=renderer,
        selection=selection,
        status_overlay=status_overlay,
        stop_event=stop_event,
        notifier=notifier,
        resize_delay_ms=config.robot_hand.resize_debounce_ms,
    )
    lifecycle.bind_root_events()

    logger.info("Loaded %s clips from %s", selection.count, clips_folder)
    selection.set_current_clip(selection.current_path)
    view.root.withdraw()
    view.root.after(16, refresh_controller.refresh)
    view.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
