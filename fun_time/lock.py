from __future__ import annotations

from dataclasses import dataclass

from funestra.file_channel import append_command

from .bridge_records import BridgeConfig, Op, WindowOp
from .content import load_web_providers
from .media_actions import ensure_in_favs, make_web_url_from_path
from .random_favs_browser import FavEntry, target_for_fav
from .rfb_tab_page import tabs_dir, write_lock_tab_page
from .room_funestras import RoomFunestra
from .shared_state import BridgeState
from .watch_stats import record_watch_event, watch_stats_path

MARKED_WEIRD = "Marked weird"
_GO_TO = "GO_TO"


def go_to_line(item: str) -> str:
    return f"{_GO_TO}|{item}"


def a_lock_holds(item: str, state: BridgeState, config: BridgeConfig) -> list[WindowOp]:
    if not item:
        return []
    ensure_in_favs(config.favs_file, item)
    record_watch_event(watch_stats_path(config.state_dir), item, "lock")
    return _where_more_of_it_is_made(item, state, config)


def _where_more_of_it_is_made(item: str, state: BridgeState,
                              config: BridgeConfig) -> list[WindowOp]:
    providers = load_web_providers()
    target = target_for_fav(
        FavEntry(local_path=item, web_url=make_web_url_from_path(item, providers)),
        config.regen, providers)
    if not target.url:
        if state.origenerator_ready and config.hosts_origenerator:
            append_command(config.origenerator_cmd_file, go_to_line(item))
        return []
    page = write_lock_tab_page(tabs_dir(config.state_dir), target,
                               loopback_port=config.loopback_port)
    return [WindowOp(op=Op.OPEN_RFB_TAB, key=page)]


@dataclass(frozen=True)
class DiscardPlan:
    condemns: bool
    log_message: str
    notice_message: str


def build_discard_plan(
    *, funestra: RoomFunestra, current_path: str, is_favorite: bool = False
) -> DiscardPlan:
    if is_favorite:
        return DiscardPlan(
            condemns=False,
            log_message=f"Removed from favorites on Funestra {funestra}: {current_path}",
            notice_message="Unfavorited")
    return DiscardPlan(
        condemns=True,
        log_message=f"Discarding from Funestra {funestra}: {current_path}",
        notice_message=MARKED_WEIRD)
