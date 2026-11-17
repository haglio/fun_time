from __future__ import annotations

from app_support.kino_folder import is_ai_video
from funestra.file_channel import append_command

from .bridge_records import BridgeConfig, Op, WindowOp
from .content import load_web_providers
from .gallery import Gallery, star_in_the_gallery, the_gallery_in, unstar_in_the_gallery
from .media_metadata import a_favorite, mark_favorite, metadata_path_for
from .random_faves_browser import FavEntry, target_for_fav, with_its_page
from .rfb_mode import switch_the_rfb_mode
from .rfb_modes import RfbMode
from .rfb_tab_page import tabs_dir, write_lock_tab_page
from .shared_state import BridgeState
from .watch_stats import record_watch_event, watch_stats_path

SENT_TO_TRASH = "Sent to Trash"
UNFAVORITED = "Unfavorited"
_GO_TO = "GO_TO"


def go_to_line(item: str) -> str:
    return f"{_GO_TO}|{item}"


def core_goes_ahead_to(item: str, state: BridgeState, config: BridgeConfig) -> bool:
    if not item:
        return False
    gallery = _the_gallery(config)
    favorite = with_its_page(item, gallery, load_web_providers())
    return not _its_page(favorite, config, gallery).url and _core_goes_to(item, state, config)


def a_lock_holds(item: str, state: BridgeState, config: BridgeConfig, *,
                 core_is_there: bool = False) -> tuple[BridgeState, list[WindowOp]]:
    if not item:
        return state, []
    gallery = _the_gallery(config)
    favorite = with_its_page(item, gallery, load_web_providers())
    if is_ai_video(item):
        star_in_the_gallery(item, config.librarys_origenerator)
    else:
        mark_favorite(item, config.regen_metadata_root, True, page=favorite.web_url)
    record_watch_event(watch_stats_path(config.state_dir), item, "lock")
    return _where_more_of_it_is_made(favorite, state, config, gallery,
                                     core_is_there=core_is_there)


def _the_gallery(config: BridgeConfig) -> Gallery:
    if config.kept_gallery is not None:
        return config.kept_gallery.latest()
    return the_gallery_in(config.librarys_origenerator)


def a_hosted_lock_holds(item: str, state: BridgeState,
                        config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    if not item:
        return state, []
    if is_ai_video(item):
        record_watch_event(watch_stats_path(config.state_dir), item, "lock")
    elif _has_a_record(item, config):
        return a_lock_holds(item, state, config)
    gallery = _the_gallery(config)
    return _where_more_of_it_is_made(with_its_page(item, gallery, load_web_providers()),
                                     state, config, gallery)


def _has_a_record(item: str, config: BridgeConfig) -> bool:
    record = metadata_path_for(item, config.regen_metadata_root)
    return record is not None and record.is_file()


def unfavorited(item: str, config: BridgeConfig) -> bool:
    if not item or not a_favorite(item, config.regen_metadata_root,
                                  lambda: _the_gallery(config)):
        return False
    if is_ai_video(item):
        unstar_in_the_gallery(item, config.librarys_origenerator)
    else:
        mark_favorite(item, config.regen_metadata_root, False)
    return True


def _its_page(favorite: FavEntry, config: BridgeConfig, gallery: Gallery):
    return target_for_fav(favorite, config.regen, load_web_providers(), gallery)


def _core_goes_to(item: str, state: BridgeState, config: BridgeConfig) -> bool:
    if state.origenerator_core_ready:
        append_command(config.origenerator.cmd_file, go_to_line(item))
    return state.origenerator_core_ready


def _where_more_of_it_is_made(favorite: FavEntry, state: BridgeState, config: BridgeConfig,
                              gallery: Gallery, *,
                              core_is_there: bool = False) -> tuple[BridgeState, list[WindowOp]]:
    target = _its_page(favorite, config, gallery)
    if not target.url:
        if not core_is_there:
            _core_goes_to(favorite.local_path, state, config)
        return state, []
    ops: list[WindowOp] = []
    if state.rfb_mode is RfbMode.ORIGENERATOR:
        state, ops = switch_the_rfb_mode(RfbMode.RFB, state, config)
    page = write_lock_tab_page(tabs_dir(config.state_dir), target,
                               loopback_port=config.loopback_port)
    return state, [*ops, WindowOp(op=Op.OPEN_RFB_TAB, key=page)]
