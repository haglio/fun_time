from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app_support.file_channel import consume_command_file

from .bridge_records import BridgeConfig
from .command_answering import CommandAnswering
from .crown import Seating
from .dashboard_actions import ENTER_OMNIPAUSE
from .dispatch_loop_setup import Publishing, SessionFiles
from .funestra_messages import FunestraMessages
from .funestra_settings import FunestraReporting
from .hud_feed import HudFeed
from .latest_arrivals import LatestArrivals
from .lock import a_lock_holds
from .origenerator_opening import OrigeneratorOpening
from .rfb_mode import switch_the_rfb_mode
from .rfb_modes import RfbMode
from .rfb_slideshow import RfbSlideshow
from .satellite_speeds import SatelliteSpeeds
from .shared_state import read_shared_state, write_shared_state
from .text_field_clicks import TypingWatch
from .voice_commands import parse_command_line

logger = logging.getLogger(__name__)


class Follower(Protocol):
    def tick(self, now: float, answering: CommandAnswering) -> None: ...


@dataclass(frozen=True)
class EachTick:
    act: Callable[[float, CommandAnswering], object]

    def tick(self, now: float, answering: CommandAnswering) -> None:
        self.act(now, answering)


class Every:
    def __init__(self, seconds: float, followers: Sequence[Follower]) -> None:
        self._seconds = seconds
        self._followers = tuple(followers)
        self._last = 0.0

    def tick(self, now: float, answering: CommandAnswering) -> None:
        if now - self._last < self._seconds:
            return
        self._last = now
        for follower in self._followers:
            follower.tick(now, answering)


def give_core_the_browsers_place_if_owed(opening: OrigeneratorOpening,
                                         answering: CommandAnswering) -> None:
    if opening.gives_core_the_browsers_place_now(answering.state):
        answering.take(*switch_the_rfb_mode(RfbMode.ORIGENERATOR, answering.state,
                                            answering.config))


class WhatTheSessionRemembers:
    def __init__(self, opening: OrigeneratorOpening) -> None:
        self._opening = opening
        self._reporting = FunestraReporting()

    def tick(self, now: float, answering: CommandAnswering) -> None:
        shared = read_shared_state(answering.shared_state_file)
        if shared is not None:
            answering.state = shared
        answering.state = self._opening.app_as_it_is(answering.state)
        give_core_the_browsers_place_if_owed(self._opening, answering)
        answering.state = self._reporting.recorded(answering.state, answering.config, now=now)
        if shared is not None and shared != answering.state:
            write_shared_state(answering.shared_state_file, answering.state)


def poll_dashboard_commands(cmd_file: Path) -> list[str]:
    return consume_command_file(cmd_file, uppercase=False)


class DashboardCommands:
    def __init__(self, cmd_file: Path) -> None:
        self._cmd_file = cmd_file

    def tick(self, now: float, answering: CommandAnswering) -> None:
        with answering.window_work.holding_a_ticks_tabs_for_one_chrome_launch():
            for line in poll_dashboard_commands(self._cmd_file):
                answering.handle_line(parse_command_line(line))


class ClicksIntoATextField:
    def __init__(self, typing: TypingWatch) -> None:
        self._typing = typing

    def tick(self, now: float, answering: CommandAnswering) -> None:
        if not self._typing.clicked_into_a_text_field(now=now):
            return
        if not answering.state.omni_paused:
            logger.info("A click into a text field enters OmniPause")
            answering.answer(ENTER_OMNIPAUSE)


class TheCrownsSeat:
    def __init__(self, seating: Seating) -> None:
        self._seating = seating

    def tick(self, now: float, answering: CommandAnswering) -> None:
        seated = self._seating.seat(answering.state, answering.window_work.windows)
        if seated is not answering.state:
            answering.keep(seated)


class LocksWhatTheMainFunestraHolds:
    def tick(self, now: float, answering: CommandAnswering) -> None:
        held = answering.lock_follower.held()
        if held:
            answering.take(*a_lock_holds(held, answering.state, answering.config))


class TheBrowsersSlideshow:
    def __init__(self, slideshow: RfbSlideshow) -> None:
        self._slideshow = slideshow

    def tick(self, now: float, answering: CommandAnswering) -> None:
        state = answering.state
        self._slideshow.tick(now=now, held=state.omni_paused
                             or state.rfb_mode is RfbMode.ORIGENERATOR)


@dataclass(frozen=True)
class LoopParts:
    config: BridgeConfig
    files: SessionFiles
    publishing: Publishing
    opening: OrigeneratorOpening
    typing: TypingWatch
    latest_arrivals: LatestArrivals
    hud: HudFeed
    rfb_slideshow: RfbSlideshow | None


def the_followers(parts: LoopParts) -> tuple[Follower, ...]:
    config = parts.config
    messages = FunestraMessages(config)
    seating = Seating(parts.publishing.secondary_rects,
                      main_funestra_status_file=config.main_funestra.status_file,
                      genau_status_file=config.genau.status_file)
    satellite_speeds = SatelliteSpeeds(
        main_funestra=config.main_funestra, satellites=config.satellites.values())
    return (
        EachTick(lambda now, answering: messages.flash_new()),
        WhatTheSessionRemembers(parts.opening),
        EachTick(lambda now, answering: answering.arbiter.follow(answering.state)),
        DashboardCommands(parts.files.dashboard_cmd),
        ClicksIntoATextField(parts.typing),
        EachTick(lambda now, answering: answering.window_work.flush_rfb_tabs(answering.state)),
        EachTick(lambda now, answering: answering.window_work.windows.flush_pending_hides()),
        TheCrownsSeat(seating),
        EachTick(lambda now, answering: answering.voice.suspend_as_the_app_is(
            paused=answering.state.omni_paused)),
        Every(parts.publishing.sync_interval_ms / 1000, (
            EachTick(lambda now, answering: answering.window_work.converge_origenerator_window(
                answering.state)),
            EachTick(lambda now, answering: satellite_speeds.take_the_main_funestras_rate()),
            EachTick(lambda now, answering: answering.update_dashboard_if_there_is_one()),
        )),
        EachTick(lambda now, answering: parts.latest_arrivals.deliver(answering.state)),
        LocksWhatTheMainFunestraHolds(),
        EachTick(lambda now, answering: answering.watch.sample_due(
            now=now, omni_paused=answering.state.omni_paused)),
        EachTick(lambda now, answering: parts.hud.publish_due(answering.state, now=now)),
        *((TheBrowsersSlideshow(parts.rfb_slideshow),) if parts.rfb_slideshow is not None else ()),
    )
