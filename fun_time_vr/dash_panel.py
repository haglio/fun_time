"""The dashboard, hanging in the headset: the control bar and the log stream.

The desktop's is a Qt window a VR session never launches, so this paints the
same thing with Pillow -- the bar's own geometry, the family's own marks and
metrics, the same action names and the same window labels, off dashboard_layout,
shared_ui and event_log rather than drawn to match.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from functools import cache

from PIL import Image, ImageDraw, ImageFont
from shared_ui.icons_pil import glyph_image
from shared_ui.palette import (
    BG_BUTTON,
    BG_PRIMARY,
    BG_TERTIARY,
    BLUE,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WHITE,
    hovered,
)
from shared_ui.spacing import (
    BUTTON_GAP,
    BUTTON_GROUP_GAP,
    BUTTON_PAD_H_TIGHT,
    BUTTON_RADIUS_HUD,
    BUTTON_SIZE_HUD,
)

from fun_time.cover_palette import WORDMARK_MAGENTA
from fun_time.dashboard_controls import bar_controls, mark_side
from fun_time.dashboard_layout import PAD, Rect, compute_dashboard_bar_layout
from fun_time.event_log import (
    LEVEL_NAMES,
    LEVELS_BY_NAME,
    SOURCE_LABELS,
    SOURCES,
    EventRecord,
)
from fun_time.icon_image import load_icon_image
from fun_time.project_paths import PROJECT_ICON

from .console_panel import level_color

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


# Pillow loads a face by file, not by family: "b" is Segoe UI's bold, "z" its
# bold italic -- the lean the app's name wears everywhere else it is written.
_BODY_FACE = "segoeuib.ttf"
_WORDMARK_FACE = "segoeuiz.ttf"


@cache
def _font(px: int, face: str = _BODY_FACE) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(face, px)
    except OSError:
        return ImageFont.load_default(px)


@dataclass(frozen=True)
class DashState:  # what the bar shows, and what the log is filtered to
    omni_paused: bool = False
    voice_active: bool = False
    # The room's own F-mode, so the bar's F lights here exactly as it does on
    # the desktop's.
    f_mode: bool = False
    reference_open: bool = False
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


def dial_rect() -> Rect:
    bar = compute_dashboard_bar_layout()
    return Rect(bar.width, bar.quit_button.y, _DIAL_W, BUTTON_SIZE_HUD)


def source_chips() -> dict[str, Rect]:  # left to right, in SOURCES order
    dial = dial_rect()
    chips: dict[str, Rect] = {}
    x = dial.x + dial.width + BUTTON_GROUP_GAP
    for source in SOURCES:
        width = int(_font(_SMALL_PX).getlength(SOURCE_LABELS[source])) + 2 * BUTTON_PAD_H_TIGHT
        chips[source] = Rect(x, dial.y, width, BUTTON_SIZE_HUD)
        x += width + BUTTON_GAP
    return chips


def _row_end() -> int:
    last = list(source_chips().values())[-1]
    return last.x + last.width


DASH_WIDTH_PX = _row_end() + PAD


def dial_stops() -> dict[str, Rect]:
    """Where each level sits in the open list, hanging under the dial."""
    dial = dial_rect()
    return {
        f"{VERBOSITY_STOP}{name}": Rect(
            dial.x, dial.y + (index + 1) * dial.height, dial.width, dial.height)
        for index, name in enumerate(LEVEL_NAMES)
    }


def dash_actions(*, dial_open: bool = False) -> dict[str, Rect]:
    """Every pressable rect, by its action; the list's stops only while open."""
    actions = {control.action: control.rect
               for control in bar_controls(compute_dashboard_bar_layout(), in_vr=True)}
    actions[VERBOSITY_CHIP] = dial_rect()
    actions.update(source_chips())
    if dial_open:  # over the log, and pressed before anything under it
        actions = dial_stops() | actions
    return actions


def _log_top() -> int:
    return compute_dashboard_bar_layout().height


def dash_height() -> int:
    return _log_top() + LOG_ROWS * _ROW_H + PAD


def _slab(draw, rect: Rect, ground, edge) -> None:
    draw.rounded_rectangle(
        (rect.x, rect.y, rect.x + rect.width - 1, rect.y + rect.height - 1),
        radius=BUTTON_RADIUS_HUD, fill=(*ground, 255), outline=(*edge, 255),
    )


def _button(draw, rect: Rect, fill, hover: tuple[int, int] | None) -> None:
    edge = TEXT_MUTED if fill == BG_BUTTON else fill
    _slab(draw, rect, hovered(fill) if _on(rect, hover) else fill, edge)


def _label(draw, rect: Rect, text: str, font, ink, *, left: bool = False) -> None:
    length = font.getlength(text)
    x = (rect.x + BUTTON_PAD_H_TIGHT if left
         else rect.x + max(2, round((rect.width - length) / 2)))
    draw.text((x, rect.y + 3), text, font=font, fill=(*ink, 255))


@cache
def _app_mark(size: int) -> Image.Image | None:
    return load_icon_image(PROJECT_ICON, size)


def _arrow_down(size: int) -> Image.Image:
    """The family's chevron turned DOWN -- Pillow rotates counter-clockwise, so
    the sign is what decides which way it ends up."""
    return glyph_image("chevron_right", size, TEXT_MUTED).rotate(-90, expand=False)


def _paint_dial(panel, draw, state: DashState, font,
                hover: tuple[int, int] | None = None) -> None:  # chrome's field
    rect = dial_rect()
    _button(draw, rect, BG_BUTTON, hover)
    _label(draw, rect, verbosity_name(state.verbosity), font, TEXT_PRIMARY, left=True)
    panel.alpha_composite(
        _arrow_down(_ARROW_PX),
        (rect.x + rect.width - _ARROW_PX - BUTTON_GAP,
         rect.y + (rect.height - _ARROW_PX) // 2),
    )


def _paint_open_list(draw, state: DashState, font) -> None:
    """The list, as chrome's menu rules dress a popup: its own ground inside one
    border, the row it is on in the dropdown blue."""
    stops = dial_stops()
    rects = list(stops.values())
    frame = Rect(rects[0].x, rects[0].y, rects[0].width,
                 rects[-1].y + rects[-1].height - rects[0].y)
    _slab(draw, frame, BG_TERTIARY, BORDER_SUBTLE)
    for action, rect in stops.items():
        name = action[len(VERBOSITY_STOP):]
        chosen = LEVELS_BY_NAME[name] == state.verbosity
        if chosen:
            draw.rectangle(
                (rect.x + 1, rect.y, rect.x + rect.width - 2, rect.y + rect.height - 1),
                fill=(*BLUE, 255),
            )
        _label(draw, rect, name, font, TEXT_PRIMARY, left=True)


def _chip(draw, rect: Rect, label: str, *, on: bool, font,
          hover: tuple[int, int] | None) -> None:
    _button(draw, rect, BLUE if on else BG_BUTTON, hover)
    _label(draw, rect, label, font, WHITE if on else TEXT_PRIMARY)


def _on(rect: Rect, point: tuple[int, int] | None) -> bool:  # is the ray on it
    if point is None:
        return False
    x, y = point
    return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height


def paint_dash(state: DashState, records,
               hover: tuple[int, int] | None = None) -> Image.Image:
    """The bar, the filter row, the log rows, the dial; *hover* lights one."""
    panel = Image.new("RGBA", (DASH_WIDTH_PX, dash_height()), (*BG_PRIMARY, 235))
    draw = ImageDraw.Draw(panel)
    bar = compute_dashboard_bar_layout()
    wordmark, small = _font(_FONT_PX, _WORDMARK_FACE), _font(_SMALL_PX)

    mark = _app_mark(bar.app_icon.height)
    if mark is not None:
        panel.alpha_composite(mark, (bar.app_icon.x, bar.app_icon.y))
    draw.text((bar.app_title.x, bar.app_title.y + 4), "Fun Time",
              font=wordmark, fill=WORDMARK_MAGENTA)
    controls = bar_controls(
        bar, omni_paused=state.omni_paused, voice_active=state.voice_active,
        f_mode=state.f_mode, in_vr=True, reference_open=state.reference_open,
    )
    for control in controls:
        rect = control.rect
        _button(draw, rect, control.lit or BG_BUTTON, hover)
        size = mark_side(rect)
        panel.alpha_composite(
            glyph_image(control.mark, size, control.ink),
            (rect.x + (rect.width - size) // 2, rect.y + (rect.height - size) // 2),
        )

    _paint_dial(panel, draw, state, small, hover=hover)
    for source, rect in source_chips().items():
        on = source in state.sources
        _chip(draw, rect, SOURCE_LABELS[source], on=on, font=small, hover=hover)

    rows = [r for r in records if state.accepts(r)][-LOG_ROWS:]
    for index, record in enumerate(rows):
        draw.text((PAD, _log_top() + index * _ROW_H), format_row(record)[:96],
                  font=small, fill=(*level_color(record.level), 255))

    if state.dial_open:
        _paint_open_list(draw, state, small)
    return panel


class DashPointer:
    """A press, turned into what the desktop's bar does: the bar's controls post
    its commands, the dial changes only what this shows."""

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

    def session_state(self, *, omni_paused: bool, voice_active: bool,
                      f_mode: bool = False, reference_open: bool = False) -> None:
        """The session's half; the filters stay this panel's."""
        self.state = replace(self.state, omni_paused=omni_paused, voice_active=voice_active,
                             f_mode=f_mode, reference_open=reference_open)
