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
from .pygame_view import PygameView
from .refresh_controller import RobotHandRefreshController
from ..config import load_config
from ..logging_utils import configure_logging, enable_faulthandler, install_exception_logging
from ..runtime_support import preparse_config_path
from ..threading_utils import start_daemon_thread
from .engine import PlaybackEngine
from .state import SharedState, udp_reader
from .video import cache_dir_for_clips_folder, load_clip_frames, scan_clips


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

    # Set AppUserModelID before any window creation so Robot Hand gets its
    # own taskbar identity (icon + title) instead of inheriting python.exe's.
    from ..win32 import APP_USER_MODEL_ID, set_app_user_model_id
    try:
        set_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        pass  # Non-fatal

    logger = configure_logging("fun_time.robot_hand", config.log_file("robot_hand_listener"))
    install_exception_logging(logger)
    fault_fp = enable_faulthandler(config.log_file("robot_hand_crash"))
    args = build_parser(config).parse_args(argv)

    logger.info("Robot Hand starting (pid=%d)", __import__("os").getpid())
    try:
        rc = run_listener(args, config, logger)
        logger.info("Robot Hand exiting normally (rc=%d)", rc)
        return rc
    except KeyboardInterrupt:
        logger.info("Robot Hand interrupted by user")
        return 0
    except Exception:
        logger.critical("Robot Hand crashed in main", exc_info=True)
        raise
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
    cache_dir = cache_dir_for_clips_folder(clips_folder)

    # Start decoding the first clip immediately in a background thread.
    # This overlaps with pygame init + controller wiring below, so by
    # the time the main loop starts the first clip is likely already
    # decoded and ready to display.
    first_clip_path = clip_sequence.current_path
    preload_result: dict = {"frames": None}

    def _preload_first_clip() -> None:
        try:
            preload_result["frames"] = load_clip_frames(first_clip_path, cache_dir)
        except Exception:
            logger.warning("Failed to pre-load first clip %s", first_clip_path, exc_info=True)

    preload_thread = None
    if first_clip_path is not None:
        preload_thread = threading.Thread(
            target=_preload_first_clip, daemon=True, name="robot-hand-preload",
        )
        preload_thread.start()

    view = PygameView(
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

    renderer = ClipRenderController(
        clip_store=clip_store,
        display_frame_fn=view.display_frame,
        logger=logger,
    )

    loader = ClipLoadController(
        clip_store=clip_store,
        load_state=load_state,
        prefetch_state=prefetch_state,
        current_clip_path_getter=lambda: renderer.current_clip_path,
        decode_clip=lambda path: load_clip_frames(path, cache_dir),
        start_thread=start_daemon_thread,
        logger=logger,
        on_active_clip_loaded=renderer.prepare_active_clip_for_current_size,
        on_error=lambda msg: state.__setattr__("error", msg),
    )
    selection = ClipSelectionController(
        sequence=clip_sequence,
        clip_store=clip_store,
        loader=loader,
        renderer=renderer,
        notifier=notifier,
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
        show_window=lambda: None,
        hide_window=lambda: None,
        set_loading_text=view.set_loading_text,
        logger=logger,
        log_name=config.log_file("robot_hand_listener").name,
        read_paused_state=read_paused_state,
    )
    lifecycle = RobotHandLifecycleController(
        view=view,
        renderer=renderer,
        selection=selection,
        stop_event=stop_event,
        notifier=notifier,
        resize_delay_ms=config.robot_hand.resize_debounce_ms,
    )

    logger.info("Loaded %s clips from %s", selection.count, clips_folder)
    if preload_thread is not None:
        preload_thread.join(timeout=10.0)
        if preload_result["frames"] is not None:
            clip_store.clip_cache[first_clip_path] = {"frames": preload_result["frames"]}
            logger.info("Pre-loaded %d frames for %s", len(preload_result["frames"]), first_clip_path.name)
    selection.set_current_clip(selection.current_path)

    while not stop_event.is_set():
        lifecycle.process_events()
        refresh_controller.refresh()
        view.clock.tick(120)

    view.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
