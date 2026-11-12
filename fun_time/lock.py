from __future__ import annotations

from app_support.kino_folder import is_ai_video
from funestra.file_channel import append_command

from .bridge_records import BridgeConfig, Op, WindowOp
from .content import load_web_providers
from .gallery import Gallery, gallery_database_in, star_in_the_gallery
from .media_metadata import mark_favorite, metadata_path_for
from .random_faves_browser import FavEntry, target_for_fav, with_its_page
from .rfb_mode import switch_the_rfb_mode
from .rfb_modes import RfbMode
from .rfb_tab_page import tabs_dir, write_lock_tab_page
from .shared_state import BridgeState
from .watch_stats import record_watch_event, watch_stats_path

SENT_TO_TRASH = "Sent to Trash"
_GO_TO = "GO_TO"


def go_to_line(item: str) -> str:
    return f"{_GO_TO}|{item}"


def a_lock_holds(item: str, state: BridgeState,
                 config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    if not item:
        return state, []
    gallery = _the_gallery(config)
    favorite = with_its_page(item, gallery, load_web_providers())
    if is_ai_video(item):
        star_in_the_gallery(item, config.librarys_origenerator)
    else:
        mark_favorite(item, config.regen_metadata_root, True, page=favorite.web_url)
    record_watch_event(watch_stats_path(config.state_dir), item, "lock")
    return _where_more_of_it_is_made(favorite, state, config, gallery)


def _the_gallery(config: BridgeConfig) -> Gallery:
    return Gallery.read(gallery_database_in(config.librarys_origenerator))


def a_hosted_lock_holds(item: str, state: BridgeState,
                        config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
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


def unfavorite(item: str, config: BridgeConfig) -> None:
    if not is_ai_video(item):
        mark_favorite(item, config.regen_metadata_root, False)


def _where_more_of_it_is_made(favorite: FavEntry, state: BridgeState, config: BridgeConfig,
                              gallery: Gallery) -> tuple[BridgeState, list[WindowOp]]:
    item = favorite.local_path
    target = target_for_fav(favorite, config.regen, load_web_providers(), gallery)
    if not target.url:
        if state.origenerator_core_ready:
            append_command(config.origenerator_cmd_file, go_to_line(item))
        return state, []
    ops: list[WindowOp] = []
    if state.rfb_mode is RfbMode.ORIGENERATOR:
        state, ops = switch_the_rfb_mode(RfbMode.RFB, state, config)
    page = write_lock_tab_page(tabs_dir(config.state_dir), target,
                               loopback_port=config.loopback_port)
    return state, [*ops, WindowOp(op=Op.OPEN_RFB_TAB, key=page)]
