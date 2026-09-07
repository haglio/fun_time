"""The dashboard, hanging in the headset: the control bar and the log stream.

The desktop's is a Qt window a VR session never launches, so this paints the
same thing with Pillow -- the bar's own geometry, the family's own marks and
metrics, the same action names and the same window labels, off dashboard_layout,
shared_ui and event_log rather than drawn to match.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from PIL import Image, ImageDraw, ImageFont
from shared_ui.icons_pil import glyph_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_TERTIARY,
    BLUE,
    BORDER_SUBTLE,
    MAGENTA,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from shared_ui.spacing import BUTTON_ICON, BUTTON_PAD_H_TIGHT, BUTTON_RADIUS

from fun_time.dashboard_actions import (
    HELP_REFERENCE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.dashboard_layout import Rect, compute_dashboard_bar_layout
from fun_time.event_log import (
    LEVEL_NAMES,
    LEVELS_BY_NAME,
    SOURCE_LABELS,
    SOURCES,
    EventRecord,
)

from .console_panel import level_color

# Wider than the console: a log row says a clock, a source and a message.
DASH_WIDTH_PX = 560

_PAD = 10
_CHIP_H = 20
_CHIP_GAP = 6
_CHIP_W = 46
_DIAL_W = 92  # the name and the arrow beside it
_ARROW_PX = 10
_ROW_H = 16
_FONT_PX = 13
_SMALL_PX = 11
LOG_ROWS = 8

# The dial, and one action per stop while it is open, named so a press carries
# which stop it landed on.
VERBOSITY_CHIP = "dash_verbosity"
VERBOSITY_STOP = "dash_verbosity:"


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
    dial_open: bool = False

    def accepts(self, record: EventRecord) -> bool:  # the log panel's own rule
        return record.level >= self.verbosity and record.source in self.sources


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
        chips[source] = Rect(x, top, _CHIP_W, _CHIP_H)
        x += _CHIP_W + _CHIP_GAP
    return chips


def _chips_top() -> int:
    return compute_dashboard_bar_layout().height + _CHIP_GAP


def dial_rect() -> Rect:
    return Rect(DASH_WIDTH_PX - _PAD - _DIAL_W, _chips_top(), _DIAL_W, _CHIP_H)


def dial_stops() -> dict[str, Rect]:
    """Where each level sits in the open list, hanging under the dial."""
    dial = dial_rect()
    return {
        f"{VERBOSITY_STOP}{name}": Rect(
            dial.x, dial.y + dial.height + index * _CHIP_H, _DIAL_W, _CHIP_H)
        for index, name in enumerate(LEVEL_NAMES)
    }


def dash_actions(*, dial_open: bool = False) -> dict[str, Rect]:
    """Every pressable rect, by its action; the list's stops only while open."""
    bar = compute_dashboard_bar_layout()
    actions = {
        QUIT_BUTTON: bar.quit_button,
        OMNIPAUSE_TOGGLE: bar.omnipause_button,
        HELP_REFERENCE: bar.help_button,
        VOICE_TOGGLE: bar.voice_panel,
        VERBOSITY_CHIP: dial_rect(),
    }
    actions.update(source_chips(_chips_top()))
    if dial_open:  # over the log, and pressed before anything under it
        actions = dial_stops() | actions
    return actions


def dash_height() -> int:
    return _chips_top() + _CHIP_H + _CHIP_GAP + LOG_ROWS * _ROW_H + _PAD


def _slab(draw, rect: Rect, ground, *, border=None) -> None:
    draw.rounded_rectangle(
        (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
        radius=BUTTON_RADIUS, fill=(*ground, 255),
        outline=None if border is None else (*border, 255),
    )


def _label(draw, rect: Rect, text: str, font, ink, *, left: bool = False) -> None:
    length = font.getlength(text)
    x = (rect.x + BUTTON_PAD_H_TIGHT if left
         else rect.x + max(2, round((rect.width - length) / 2)))
    draw.text((x, rect.y + 3), text, font=font, fill=(*ink, 255))


def _arrow_down(size: int) -> Image.Image:  # the family's chevron, turned
    return glyph_image("chevron_right", size, TEXT_MUTED).rotate(90, expand=False)


def _paint_dial(panel: Image.Image, draw, state: DashState, font) -> None:
    """The closed field: bordered, the level in it, the arrow at its right --
    ``shared_ui.chrome``'s button rules, drawn."""
    rect = dial_rect()
    _slab(draw, rect, BG_BUTTON, border=BORDER_SUBTLE)
    _label(draw, rect, verbosity_name(state.verbosity), font, TEXT_PRIMARY, left=True)
    panel.alpha_composite(
        _arrow_down(_ARROW_PX),
        (rect.x + rect.width - _ARROW_PX - BUTTON_PAD_H_TIGHT,
         rect.y + (rect.height - _ARROW_PX) // 2),
    )


def _paint_open_list(draw, state: DashState, font) -> None:
    """The list under it, as ``shared_ui.chrome``'s menu rules dress a popup:
    its own ground inside one border, the current row in the dropdown blue."""
    stops = dial_stops()
    rects = list(stops.values())
    frame = Rect(rects[0].x, rects[0].y, rects[0].width,
                 rects[-1].y + rects[-1].height - rects[0].y)
    _slab(draw, frame, BG_TERTIARY, border=BORDER_SUBTLE)
    for action, rect in stops.items():
        name = action[len(VERBOSITY_STOP):]
        chosen = LEVELS_BY_NAME[name] == state.verbosity
        if chosen:
            draw.rectangle(
                (rect.x + 1, rect.y, rect.x + rect.width - 2, rect.y + rect.height - 1),
                fill=(*BLUE, 255),
            )
        _label(draw, rect, name, font, TEXT_PRIMARY, left=True)


def _chip(draw, rect: Rect, label: str, *, on: bool, font) -> None:
    """A word-button: one ground always, the setting carried by the label."""
    _slab(draw, rect, BG_BUTTON)
    _label(draw, rect, label, font, TEXT_PRIMARY if on else TEXT_MUTED)


def paint_dash(state: DashState, records) -> Image.Image:
    """The bar, the filter row, the visible log rows, and the open dial over them."""
    panel = Image.new("RGBA", (DASH_WIDTH_PX, dash_height()), (*BG_PRIMARY, 235))
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
        _slab(draw, rect,
              BLUE if (action == VOICE_TOGGLE and state.voice_active) else BG_BUTTON)
        size = min(BUTTON_ICON, min(rect.width, rect.height))
        panel.alpha_composite(
            glyph_image(mark, size, TEXT_PRIMARY),
            (rect.x + (rect.width - size) // 2, rect.y + (rect.height - size) // 2),
        )

    _paint_dial(panel, draw, state, small)
    for source, rect in source_chips(_chips_top()).items():
        _chip(draw, rect, SOURCE_LABELS.get(source, source), on=source in state.sources,
              font=small)

    rows = [r for r in records if state.accepts(r)][-LOG_ROWS:]
    top = _chips_top() + _CHIP_H + _CHIP_GAP
    for index, record in enumerate(rows):
        draw.text((_PAD, top + index * _ROW_H), format_row(record)[:96],
                  font=small, fill=(*level_color(record.level), 255))

    if state.dial_open:
        _paint_open_list(draw, state, small)
    return panel


class DashPointer:
    """A press, turned into what the desktop's bar does: the four controls post
    its commands, the dial and the window buttons change only what this shows."""

    def __init__(self, *, post, state: DashState | None = None) -> None:
        self._post = post
        self.state = state or DashState()

    def press(self, px: int, py: int) -> str | None:  # the action, or None
        for action, rect in dash_actions(dial_open=self.state.dial_open).items():
            if not (rect.x <= px < rect.x + rect.width
                    and rect.y <= py < rect.y + rect.height):
                continue
            self._act(action)
            return action
        # Anywhere else closes an open list, as clicking off a dropdown does.
        self.state = replace(self.state, dial_open=False)
        return None

    def _act(self, action: str) -> None:
        if action == VERBOSITY_CHIP:
            self.state = replace(self.state, dial_open=not self.state.dial_open)
        elif action.startswith(VERBOSITY_STOP):
            self.state = replace(
                self.state, dial_open=False,
                verbosity=LEVELS_BY_NAME[action[len(VERBOSITY_STOP):]],
            )
        elif action in SOURCES:
            self.state = replace(
                self.state, dial_open=False,
                sources=frozenset(set(self.state.sources) ^ {action}),
            )
        else:
            self.state = replace(self.state, dial_open=False)
            self._post(action)

    def session_state(self, *, omni_paused: bool, voice_active: bool) -> None:
        """The session's half; the filters stay this panel's."""
        self.state = replace(
            self.state, omni_paused=omni_paused, voice_active=voice_active)
