"""The dashboard, hanging in the headset: the control bar and the log stream.

The desktop's is a Qt window a VR session never launches, so this paints the
same thing with Pillow -- the bar's own geometry, the family's own marks and the
same action names, off dashboard_layout, shared_ui.icons_pil and
dashboard_actions rather than drawn to match.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont
from shared_ui.icons_pil import glyph_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_BUTTON_ACTIVE,
    BG_PRIMARY,
    BLUE,
    MAGENTA,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

from fun_time.dashboard_actions import (
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.dashboard_layout import Rect, compute_dashboard_bar_layout
from fun_time.event_log import LEVEL_NAMES, LEVELS_BY_NAME, SOURCES, EventRecord

from .console_panel import level_color

# Wider than the console: a log row says a clock, a source and a message.
DASH_WIDTH_PX = 560

_PAD = 10
_CHIP_H = 20
_CHIP_GAP = 6
_ROW_H = 16
_FONT_PX = 13
_SMALL_PX = 11
LOG_ROWS = 8

VERBOSITY_CHIP = "dash_verbosity"  # handled here, as the desktop's dial is


def _font(px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeuib.ttf", px)
    except OSError:
        return ImageFont.load_default(px)


@dataclass(frozen=True)
class DashState:  # what the bar shows, and what the log is filtered to
    omni_paused: bool = False
    voice_active: bool = False
    verbosity: int = LEVELS_BY_NAME["NOTICE"]
    sources: frozenset[str] = frozenset(SOURCES)

    def accepts(self, record: EventRecord) -> bool:  # the log panel's own rule
        return record.level >= self.verbosity and record.source in self.sources


def next_verbosity(verbosity: int) -> int:  # wrapping: a headset has no dropdown
    stops = [LEVELS_BY_NAME[name] for name in LEVEL_NAMES]
    return stops[(stops.index(verbosity) + 1) % len(stops)] if verbosity in stops else stops[0]


def verbosity_name(verbosity: int) -> str:
    named = {level: name for name, level in LEVELS_BY_NAME.items()}
    return named.get(verbosity, "NOTICE")


def format_row(record: EventRecord) -> str:  # the desktop panel's own shape
    clock = time.strftime("%H:%M:%S", time.localtime(record.ts))
    return f"{clock}  {record.source:<9}  {record.message}"


def source_chips(top: int) -> dict[str, Rect]:  # left to right, in SOURCES order
    chips: dict[str, Rect] = {}
    x = _PAD
    for source in SOURCES:
        width = 46
        chips[source] = Rect(x, top, width, _CHIP_H)
        x += width + _CHIP_GAP
    return chips


def dash_actions() -> dict[str, Rect]:  # every pressable rect, by its action
    bar = compute_dashboard_bar_layout()
    chips_top = bar.height + _CHIP_GAP
    actions = {
        QUIT_BUTTON: bar.quit_button,
        OMNIPAUSE_TOGGLE: bar.omnipause_button,
        HELP_REFERENCE: bar.help_button,
        VOICE_TOGGLE: bar.voice_panel,
        VERBOSITY_CHIP: Rect(DASH_WIDTH_PX - _PAD - 74, chips_top, 74, _CHIP_H),
    }
    actions.update(source_chips(chips_top))
    return actions


def dash_height() -> int:
    return (compute_dashboard_bar_layout().height + _CHIP_GAP + _CHIP_H
            + _CHIP_GAP + LOG_ROWS * _ROW_H + _PAD)


def _chip(draw, rect: Rect, label: str, *, on: bool, font) -> None:
    ground = BG_BUTTON_ACTIVE if on else BG_BUTTON
    draw.rounded_rectangle(
        (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
        radius=4, fill=(*ground, 255),
    )
    ink = TEXT_PRIMARY if on else TEXT_MUTED
    length = font.getlength(label)
    draw.text((rect.x + max(2, (rect.width - length) // 2), rect.y + 3),
              label, font=font, fill=(*ink, 255))


def paint_dash(state: DashState, records) -> Image.Image:
    """The bar, the filter chips, and the visible log rows."""
    height = dash_height()
    panel = Image.new("RGBA", (DASH_WIDTH_PX, height), (*BG_PRIMARY, 235))
    draw = ImageDraw.Draw(panel)
    bar = compute_dashboard_bar_layout()
    actions = dash_actions()
    body, small = _font(_FONT_PX), _font(_SMALL_PX)

    draw.text((bar.app_title.x, bar.app_title.y + 4), "Fun Time",
              font=body, fill=(*MAGENTA, 255))
    marks = {
        QUIT_BUTTON: "power",
        OMNIPAUSE_TOGGLE: "play" if state.omni_paused else "pause",
        HELP_REFERENCE: "question",
        VOICE_TOGGLE: "mic",
    }
    for action, mark in marks.items():
        rect = actions[action]
        ground = BLUE if (action == VOICE_TOGGLE and state.voice_active) else BG_BUTTON
        draw.rounded_rectangle(
            (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
            radius=4, fill=(*ground, 255),
        )
        size = int(rect.height * 0.6)
        panel.alpha_composite(
            glyph_image(mark, size, TEXT_PRIMARY),
            (rect.x + (rect.width - size) // 2, rect.y + (rect.height - size) // 2),
        )

    _chip(draw, actions[VERBOSITY_CHIP], verbosity_name(state.verbosity),
          on=state.verbosity != LEVELS_BY_NAME["NOTICE"], font=small)
    for source, rect in source_chips(bar.height + _CHIP_GAP).items():
        _chip(draw, rect, source[:4].title(), on=source in state.sources, font=small)

    rows = [r for r in records if state.accepts(r)][-LOG_ROWS:]
    top = bar.height + _CHIP_GAP + _CHIP_H + _CHIP_GAP
    for index, record in enumerate(rows):
        draw.text((_PAD, top + index * _ROW_H), format_row(record)[:96],
                  font=small, fill=(*level_color(record.level), 255))
    return panel


class DashPointer:
    """A press, turned into what the desktop's bar does: the four controls post
    its commands, the chips change only what this shows."""

    def __init__(self, *, post, state: DashState | None = None) -> None:
        self._post = post
        self.state = state or DashState()

    def press(self, px: int, py: int) -> str | None:  # the action, or None
        for action, rect in dash_actions().items():
            if not (rect.x <= px < rect.x + rect.width
                    and rect.y <= py < rect.y + rect.height):
                continue
            if action == VERBOSITY_CHIP:
                self.state = DashState(
                    self.state.omni_paused, self.state.voice_active,
                    next_verbosity(self.state.verbosity), self.state.sources,
                )
            elif action in SOURCES:
                sources = set(self.state.sources) ^ {action}
                self.state = DashState(
                    self.state.omni_paused, self.state.voice_active,
                    self.state.verbosity, frozenset(sources),
                )
            else:
                self._post(action)
            return action
        return None

    def session_state(self, *, omni_paused: bool, voice_active: bool) -> None:
        """The session's half; the filters stay this panel's."""
        self.state = DashState(
            omni_paused, voice_active, self.state.verbosity, self.state.sources,
        )


