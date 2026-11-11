from __future__ import annotations

import logging
import winsound
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

from funestra.osr2_controls_host import Osr2ControlsHost
from PIL import Image, ImageDraw
from shared_ui.button_pil import button, slab
from shared_ui.dropdown_pil import paint_field, paint_list
from shared_ui.icons_pil import pil_icon
from shared_ui.lettering import WORDMARK_FACE, load_font, wrap_text
from shared_ui.loading_panel import icon_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_TERTIARY,
    BLACK,
    BLUE,
    BORDER_SUBTLE,
    TEXT_PRIMARY,
    WHITE,
)
from shared_ui.preview import Preview
from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_PAD_H_TIGHT,
    BUTTON_SIZE_HUD,
    GAP_SMALL,
)
from shared_ui.type_scale import SIZE_SMALL, SIZE_TINY, in_pixels

from fun_time import preview_marker
from fun_time.dashboard_actions import QUIT_BUTTON, QUIT_QUESTION
from fun_time.dashboard_controls import BarControl, BarState, bar_controls, icon_side
from fun_time.dashboard_layout import PAD, Rect, compute_dashboard_bar_layout
from fun_time.dashboard_osr2_model import room_for_a_whole_part
from fun_time.event_log import (
    LEVEL_NAMES,
    LEVELS_BY_NAME,
    SOURCE_LABELS,
    SOURCES,
)
from fun_time.level_colors import level_rgb
from fun_time.log_panel_model import (
    DEFAULT_UI_STATE,
    LogFilter,
    LogPanelState,
    format_record,
    load_ui_state,
    save_ui_state,
    visible_records,
)
from fun_time.project_paths import PROJECT_ICON

from .main_hud import DEG_PER_PX

_DIAL_W = 92
_ROW_H = 16
_WORDMARK_PX = in_pixels(SIZE_SMALL)
_LABEL_PX = in_pixels(SIZE_TINY)
LOG_ROWS = 8

VERBOSITY_DROPDOWN = "dash_verbosity"
VERBOSITY_STOP = "dash_verbosity:"
QUIT_YES = "dash_quit:yes"
QUIT_NO = "dash_quit:no"
_ANSWERS = {QUIT_YES: "Yes", QUIT_NO: "No"}
_DIMMED_BEHIND_THE_QUESTION = 140


@dataclass(frozen=True)
class DashState:
    omni_paused: bool = False
    voice_active: bool = False
    f_mode: bool = False
    reference_open: bool = False
    nothing_to_reset: bool = False
    verbosity: int = DEFAULT_UI_STATE.verbosity
    sources: frozenset[str] = DEFAULT_UI_STATE.sources
    dial_open: bool = False
    asking_to_quit: bool = False

    def log_filter(self) -> LogFilter:
        return LogFilter(verbosity=self.verbosity, sources=self.sources)

    def controls(self) -> tuple[BarControl, ...]:
        return bar_controls(compute_dashboard_bar_layout(), BarState(
            omni_paused=self.omni_paused, voice_active=self.voice_active,
            f_mode=self.f_mode, in_vr=True, reference_open=self.reference_open,
            nothing_to_reset=self.nothing_to_reset,
        ))

    def dims(self, action: str) -> bool:
        return any(control.action == action and control.dim
                   for control in self.controls())


def verbosity_name(verbosity: int) -> str:
    return logging.getLevelName(verbosity)


def dial_rect() -> Rect:
    return Rect(PAD, compute_dashboard_bar_layout().height + PAD, _DIAL_W, BUTTON_SIZE_HUD)


def source_buttons() -> dict[str, Rect]:
    dial = dial_rect()
    buttons_of_a_row: dict[str, Rect] = {}
    x = dial.x + dial.width + BUTTON_GROUP_GAP
    for source in SOURCES:
        width = int(load_font(_LABEL_PX).getlength(SOURCE_LABELS[source])) + 2 * BUTTON_PAD_H_TIGHT
        buttons_of_a_row[source] = Rect(x, dial.y, width, BUTTON_SIZE_HUD)
        x += width + BUTTON_GAP
    return buttons_of_a_row


@cache
def osr2_part_rect() -> Rect:
    return Rect(compute_dashboard_bar_layout().width, 0, *room_for_a_whole_part())


DASH_WIDTH_PX = osr2_part_rect().x + osr2_part_rect().width
DASH_DEG_PER_PX = DEG_PER_PX


def dial_stops() -> dict[str, Rect]:
    dial = dial_rect()
    return {
        f"{VERBOSITY_STOP}{name}": Rect(
            dial.x, dial.y + (index + 1) * dial.height, dial.width, dial.height)
        for index, name in enumerate(LEVEL_NAMES)
    }


def _answer_width(label: str) -> int:
    return int(load_font(_LABEL_PX).getlength(label)) + 2 * BUTTON_PAD_H_TIGHT


def question_rect() -> Rect:
    bar = compute_dashboard_bar_layout()
    answers = sum(_answer_width(label) for label in _ANSWERS.values()) + BUTTON_GAP
    width = int(load_font(_LABEL_PX).getlength(QUIT_QUESTION)) + BUTTON_GROUP_GAP + answers + 2 * PAD
    height = BUTTON_SIZE_HUD + 2 * PAD
    return Rect((bar.width - width) // 2, (dash_height() - height) // 2, width, height)


def answer_rects() -> dict[str, Rect]:
    box = question_rect()
    x = box.x + box.width - PAD
    rects: dict[str, Rect] = {}
    for action, label in reversed(_ANSWERS.items()):
        x -= _answer_width(label)
        rects[action] = Rect(x, box.y + PAD, _answer_width(label), BUTTON_SIZE_HUD)
        x -= BUTTON_GAP
    return {action: rects[action] for action in _ANSWERS}


def dash_actions(*, dial_open: bool = False, asking_to_quit: bool = False) -> dict[str, Rect]:
    if asking_to_quit:
        return answer_rects()
    actions = {control.action: control.rect
               for control in bar_controls(compute_dashboard_bar_layout(), BarState(in_vr=True))}
    actions[VERBOSITY_DROPDOWN] = dial_rect()
    actions.update(source_buttons())
    if dial_open:
        actions = dial_stops() | actions
    return actions


def _log_top() -> int:
    dial = dial_rect()
    return dial.y + dial.height + GAP_SMALL


def dash_height() -> int:
    return max(_log_top() + LOG_ROWS * _ROW_H + PAD, osr2_part_rect().height)


def size_it_hangs_still_at() -> tuple[int, int]:
    return DASH_WIDTH_PX, dash_height()


def _log_size() -> tuple[int, int]:
    return osr2_part_rect().x - 2 * PAD, dash_height() - PAD - _log_top()


def log_lines(state: DashState, records) -> list[tuple[str, int]]:
    font, (width, height) = load_font(_LABEL_PX), _log_size()
    room = height // _ROW_H
    shown: list[tuple[str, int]] = []
    for record in reversed(visible_records(records, state.log_filter())):
        lines = [(line, record.level) for line in wrap_text(font, format_record(record), width)]
        if shown and len(shown) + len(lines) > room:
            break
        shown[:0] = lines
    return shown[:room]


def _label_centered_as_a_button_is(draw, rect: Rect, text: str, font, ink) -> None:
    x = rect.x + max(2, round((rect.width - font.getlength(text)) / 2))
    draw.text((x, rect.y + 3), text, font=font, fill=(*ink, 255))


@cache
def _app_mark(size: int, shown_as: Preview | None) -> Image.Image | None:
    icon = icon_image(preview_marker.icon_file(PROJECT_ICON, shown_as))
    return None if icon is None else icon.resize((size, size), Image.LANCZOS)


_TIP_PAD = GAP_SMALL


def _paint_tip(draw, under: Rect, text: str, font) -> None:
    tip = Rect(under.x, under.y + under.height + 2,
               round(font.getlength(text)) + 2 * _TIP_PAD, _ROW_H + _TIP_PAD)
    slab(draw, tip, BG_TERTIARY, BORDER_SUBTLE)
    draw.text((tip.x + _TIP_PAD, tip.y + _TIP_PAD // 2), text, font=font,
              fill=(*TEXT_PRIMARY, 255))


def _open_list(state: DashState) -> list[tuple[Rect, str, bool]]:
    named = ((action[len(VERBOSITY_STOP):], rect) for action, rect in dial_stops().items())
    return [(rect, name, LEVELS_BY_NAME[name] == state.verbosity) for name, rect in named]


def _on(rect: Rect, point: tuple[int, int] | None) -> bool:
    if point is None:
        return False
    x, y = point
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


def _log_clipped_without_ellipsis_as_the_desktops_is(state: DashState, records, font) -> Image.Image:
    log = Image.new("RGBA", _log_size(), (0, 0, 0, 0))
    pen = ImageDraw.Draw(log)
    for index, (line, level) in enumerate(log_lines(state, records)):
        pen.text((0, index * _ROW_H), line, font=font, fill=(*level_rgb(level), 255))
    return log


def paint_dash(state: DashState, records,
               hover: tuple[int, int] | None = None,
               shown_as: Preview | None = None) -> Image.Image:
    panel = Image.new("RGBA", size_it_hangs_still_at(), (*BG_PRIMARY, 235))
    draw = ImageDraw.Draw(panel)
    bar = compute_dashboard_bar_layout()
    wordmark, small = load_font(_WORDMARK_PX, WORDMARK_FACE), load_font(_LABEL_PX)

    icon = _app_mark(bar.app_icon.height, shown_as)
    if icon is not None:
        panel.alpha_composite(icon, (bar.app_icon.x, bar.app_icon.y))
    draw.text((bar.app_title.x, bar.app_title.y + 4), preview_marker.APP_TITLE,
              font=wordmark, fill=preview_marker.wordmark_ink(shown_as))
    for control in state.controls():
        rect = control.rect
        button(draw, rect, control.lit or BG_BUTTON, lit=not control.dim and _on(rect, hover))
        size = icon_side(rect)
        panel.alpha_composite(
            pil_icon(control.icon, size, control.ink),
            (rect.x + (rect.width - size) // 2, rect.y + (rect.height - size) // 2),
        )

    paint_field(panel, dial_rect(), verbosity_name(state.verbosity), small,
                lit=_on(dial_rect(), hover))
    for source, rect in source_buttons().items():
        on = source in state.sources
        button(draw, rect, BLUE if on else BG_BUTTON, lit=_on(rect, hover))
        _label_centered_as_a_button_is(draw, rect, SOURCE_LABELS[source], small, WHITE if on else TEXT_PRIMARY)

    panel.alpha_composite(
        _log_clipped_without_ellipsis_as_the_desktops_is(state, records, small), (PAD, _log_top()))

    if state.dial_open:
        paint_list(panel, _open_list(state), small)
    if shown_as is not None and _on(bar.app_title, hover):
        _paint_tip(draw, bar.app_title, preview_marker.app_title(shown_as), small)
    if state.asking_to_quit:
        _paint_the_quit_question(panel, small, hover)
    return panel


def _paint_the_quit_question(panel: Image.Image, font, hover) -> None:
    panel.alpha_composite(Image.new("RGBA", panel.size, (*BLACK, _DIMMED_BEHIND_THE_QUESTION)))
    draw = ImageDraw.Draw(panel)
    box = question_rect()
    slab(draw, box, BG_TERTIARY, BORDER_SUBTLE)
    draw.text((box.x + PAD, box.y + PAD + 3), QUIT_QUESTION, font=font, fill=(*TEXT_PRIMARY, 255))
    for action, rect in answer_rects().items():
        button(draw, rect, BG_BUTTON, lit=_on(rect, hover))
        _label_centered_as_a_button_is(draw, rect, _ANSWERS[action], font, TEXT_PRIMARY)


def _within_the_part(px: int, py: int) -> tuple[int, int]:
    place = osr2_part_rect()
    return px - place.x, py - place.y


def with_the_osr2_part(dash: Image.Image,
                       drawing: tuple[bytes, tuple[int, int]] | None) -> Image.Image:
    if drawing is None:
        return dash
    raw, size = drawing
    place = osr2_part_rect()
    shown = dash.copy()
    shown.alpha_composite(Image.frombytes("RGBA", size, raw), (place.x, place.y))
    return shown


class DashPointer:
    def __init__(self, *, post, state: DashState | None = None, filter_file: Path | None = None,
                 alert: Callable[[], None] = lambda: winsound.MessageBeep(
                     winsound.MB_ICONEXCLAMATION)) -> None:
        self._post = post
        self._alert = alert
        self._filter_file = filter_file
        self.state = state or DashState()
        if filter_file is not None:
            kept = load_ui_state(filter_file)
            self.state = replace(self.state, verbosity=kept.verbosity, sources=kept.sources)
        self.part = Osr2ControlsHost(post=post)
        self._held = self._held_on_the_part = False

    def press(self, px: int, py: int) -> str | None:
        self._held, self._held_on_the_part = True, _on(osr2_part_rect(), (px, py))
        if self.state.asking_to_quit:
            self._answer(px, py)
            return None
        if self._held_on_the_part:
            self.part.press(*_within_the_part(px, py))
        for action, rect in dash_actions(dial_open=self.state.dial_open).items():
            if not _on(rect, (px, py)):
                continue
            if self.state.dims(action):
                break
            self._act(action)
            return action
        self.state = replace(self.state, dial_open=False)
        return None

    def _answer(self, px: int, py: int) -> None:
        self.state = replace(self.state, asking_to_quit=False)
        if _on(answer_rects()[QUIT_YES], (px, py)):
            self._post(QUIT_BUTTON)

    def drag(self, px: int, py: int) -> None:
        if self._held_on_the_part:
            self.part.move(*_within_the_part(px, py))

    def release(self) -> None:
        self._held = self._held_on_the_part = False
        self.part.release()

    def hover(self, at: tuple[int, int] | None) -> None:
        if self._held:
            return
        if at is not None and _on(osr2_part_rect(), at):
            self.part.move(*_within_the_part(*at))
        else:
            self.part.leave()

    def _act(self, action: str) -> None:
        if action == VERBOSITY_DROPDOWN:
            self.state = replace(self.state, dial_open=not self.state.dial_open)
        elif action.startswith(VERBOSITY_STOP):
            self.state = replace(
                self.state, dial_open=False,
                verbosity=LEVELS_BY_NAME[action[len(VERBOSITY_STOP):]],
            )
            self._keep_the_filter()
        elif action in SOURCES:
            self.state = replace(
                self.state, dial_open=False,
                sources=frozenset(set(self.state.sources) ^ {action}),
            )
            self._keep_the_filter()
        elif action == QUIT_BUTTON:
            self.state = replace(self.state, dial_open=False, asking_to_quit=True)
            self._alert()
        else:
            self.state = replace(self.state, dial_open=False)
            self._post(action)

    def _keep_the_filter(self) -> None:
        if self._filter_file is not None:
            save_ui_state(self._filter_file, LogPanelState(
                verbosity=self.state.verbosity, sources=self.state.sources))

    def session_state(self, *, omni_paused: bool, voice_active: bool,
                      f_mode: bool = False, reference_open: bool = False,
                      nothing_to_reset: bool = False) -> None:
        self.state = replace(self.state, omni_paused=omni_paused, voice_active=voice_active,
                             f_mode=f_mode, reference_open=reference_open,
                             nothing_to_reset=nothing_to_reset)
