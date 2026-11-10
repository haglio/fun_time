from __future__ import annotations

import argparse
import configparser
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from math import ceil
from pathlib import Path

from app_support.logging_utils import install_exception_logging
from app_support.win32 import set_app_user_model_id
from funestra.file_channel import append_command
from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from shared_ui.application import application
from shared_ui.colors import (
    BG_BUTTON,
    BG_BUTTON_ACTIVE,
    BG_PRIMARY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    dress_application,
    hovered,
)
from shared_ui.fonts import FONT_UI, SIZE_BODY
from shared_ui.foreground import bring_to_front
from shared_ui.hard_to_miss import sure
from shared_ui.icon_geometry import tooltip_for
from shared_ui.icons import icon_pixmap
from shared_ui.pressable_drawing import PressableDrawing
from shared_ui.preview import Preview
from shared_ui.preview_icon import app_icon
from shared_ui.reference import REFERENCE_WINDOW_TITLE
from shared_ui.spacing import BUTTON_RADIUS_HUD

from fun_time import preview_marker
from fun_time.config import LayoutConfig
from fun_time.dashboard_actions import (
    ENTER_OMNIPAUSE,
    ENTER_VR,
    EXIT_VR,
    FMODE_TOGGLE,
    HELP_REFERENCE,
    HELP_REFERENCE_CLOSE,
    OMNIMINIMIZE,
    OMNIPAUSE_TOGGLE,
    OMNIRESTORE,
    QUIT_BUTTON,
    QUIT_QUESTION,
    QUIT_TITLE,
    RESET_ALL,
    VOICE_TOGGLE,
)
from fun_time.dashboard_bridge import DashboardSnapshot
from fun_time.dashboard_controls import BarControl, BarState, bar_controls, icon_side
from fun_time.dashboard_layout import (
    PAD,
    RFB_MODE_SELECTOR_HEIGHT,
    DashboardBarLayout,
    Rect,
    add_rect_arguments,
    client_rect_filling_frame,
    compute_dashboard_bar_layout,
    rect_from_arguments,
)
from fun_time.dashboard_osr2 import Osr2PartWidget
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.filter_vocab import Acts
from fun_time.hosted_contract import published_actions_heard_as
from fun_time.loading_reveal import LoadingReveal
from fun_time.log_panel import LogPanelWidget
from fun_time.loopback_inbox import PRESS_PORT_FILENAME, LoopbackInbox
from fun_time.manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.message_feed import FLASH_POLL_MS, MessageFeed, screens_if_known
from fun_time.message_overlay import MessageOverlay
from fun_time.press_flash import PressFlash
from fun_time.project_paths import PROJECT_DIR, PROJECT_ICON
from fun_time.reference_dialog import ReferenceDialog
from fun_time.rfb_mode_selector import RfbModeSelector
from fun_time.session_end import mark_session_end
from fun_time.win32 import keep_in_topmost_band, set_taskbar_window_styles

logger = logging.getLogger(__name__)

BAR_REFRESH_MS = 500


def lighten_color(color: QColor, amount: int = 50) -> QColor:
    return QColor(
        min(255, color.red() + amount),
        min(255, color.green() + amount),
        min(255, color.blue() + amount),
    )


@dataclass(frozen=True)
class DashboardAppConfig:
    layout: LayoutConfig
    manifest_path: Path
    state_dir: Path
    dashboard_state_file: Path
    dashboard_cmd_file: Path


DashboardLaunchGeometry = Rect


@dataclass(frozen=True)
class DashboardTextItem:
    text: str
    rect: Rect
    color: QColor
    font: QFont


@dataclass(frozen=True)
class DashboardImageItem:
    pixmap: QPixmap
    rect: Rect


@dataclass(frozen=True)
class DashboardRectItem:
    rect: Rect
    outline: QColor
    fill: QColor


@dataclass(frozen=True)
class DashboardScene:
    width: int
    height: int
    rects: tuple[DashboardRectItem, ...]
    texts: tuple[DashboardTextItem, ...]
    actions: tuple[tuple[str, Rect], ...]
    hover_texts: tuple[tuple[Rect, str], ...] = ()
    images: tuple[DashboardImageItem, ...] = ()

    def action_at(self, x: int, y: int) -> str | None:
        return next((action for action, rect in self.actions if rect.holds(x, y)), None)

    def control_at(self, x: int, y: int) -> Rect | None:
        return next((rect for _action, rect in self.actions if rect.holds(x, y)), None)

    def name_at(self, x: int, y: int) -> str | None:
        return next((text for rect, text in self.hover_texts if rect.holds(x, y)), None)


def load_dashboard_app_config(manifest_path: Path) -> DashboardAppConfig:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(manifest_path, encoding="utf-8")

    layout = LayoutConfig(
        primary_monitor=parser.getint("layout", "primary_monitor"),
        secondary_monitor=parser.getint("layout", "secondary_monitor"),
        main_top_ratio=parser.getfloat("layout", "main_top_ratio"),
        landscape_width_ratio=parser.getfloat("layout", "landscape_width_ratio"),
    )
    state_dir = manifest_path.parent
    return DashboardAppConfig(
        layout=layout,
        manifest_path=manifest_path,
        state_dir=state_dir,
        dashboard_state_file=state_dir / parser.get(
            "commands", "dashboard_state_file", fallback="dashboard_state.ini"),
        dashboard_cmd_file=state_dir / parser.get(
            "commands", "dashboard_cmd_file", fallback="dashboard_cmd.txt"),
    )


class IconCache:
    def __init__(self) -> None:
        self._icons: dict[tuple[str, int], QPixmap] = {}
        self._family_icons: dict[tuple[str, int, int], QPixmap] = {}

    def icon(self, path: Path, height: int) -> QPixmap:
        key = (str(path), height)
        if key not in self._icons:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pm = pm.scaled(
                    height, height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._icons[key] = pm
        return self._icons[key]

    def family_icon(self, name: str, rect, color: QColor | None = None) -> QPixmap:
        ink = color or TEXT_PRIMARY
        key = (name, rect.width, rect.height, ink.rgba())
        if key not in self._family_icons:
            self._family_icons[key] = icon_pixmap(name, icon_side(rect), ink)
        return self._family_icons[key]



_BUTTON_RADIUS = BUTTON_RADIUS_HUD

_ACTION_TOOLTIPS: dict[str, str] = {
    QUIT_BUTTON: "Quit",
    OMNIPAUSE_TOGGLE: "Pause everything",
    HELP_REFERENCE: REFERENCE_WINDOW_TITLE,
    VOICE_TOGGLE: "Voice",
    FMODE_TOGGLE: "F-mode on every Funestra",
    RESET_ALL: ("Reset every Funestra — no filter, no lock, no loop, no F-mode, "
                "normal speed, shuffled from the top"),
    ENTER_VR: "Enter VR — end this session and open Fun Time VR",
    EXIT_VR: "Exit VR — end this session and open Fun Time on the desktop",
}
OMNIPAUSE_RESUME_TOOLTIP = "Play everything"


def build_dashboard_scene(
    layout: DashboardBarLayout,
    state: BarState,
    *,
    icons: IconCache,
    pressed_actions: frozenset[str] = frozenset(),
    shown_as: Preview | None = None,
) -> DashboardScene:
    omni_paused = state.omni_paused
    controls = bar_controls(layout, state)

    def _rect(control: BarControl) -> DashboardRectItem:
        if control.lit is None:
            fill, outline = BG_BUTTON, TEXT_MUTED
            pressed = BG_BUTTON_ACTIVE
        else:
            fill = outline = QColor(*control.lit)
            pressed = lighten_color(fill)
        if control.action in pressed_actions:
            fill = pressed
        return DashboardRectItem(control.rect, outline=outline, fill=fill)

    rects = tuple(_rect(control) for control in controls)
    # The app-name lockup, styled like the loading screen: bold italic, wordmark tone.
    # Built fresh (not via the cached make_font) so setItalic cannot leak into
    # every other user of a shared QFont.
    _font_app = QFont(FONT_UI, SIZE_BODY)
    _font_app.setBold(True)
    _font_app.setItalic(True)

    texts = (
        DashboardTextItem(preview_marker.APP_TITLE, layout.app_title,
                          color=QColor(*preview_marker.wordmark_ink(shown_as)), font=_font_app),
    )
    images = (
        DashboardImageItem(icons.icon(preview_marker.icon_file(PROJECT_ICON, shown_as),
                                      layout.app_icon.height), layout.app_icon),
        *(DashboardImageItem(icons.family_icon(control.icon, control.rect, QColor(*control.ink)),
                             control.rect)
          for control in controls),
    )
    tooltips = dict(_ACTION_TOOLTIPS)
    if omni_paused:
        tooltips[OMNIPAUSE_TOGGLE] = OMNIPAUSE_RESUME_TOOLTIP
    return DashboardScene(
        width=layout.width,
        height=layout.height,
        rects=rects,
        texts=texts,
        images=images,
        actions=tuple((control.action, control.rect)
                      for control in controls if not control.dim),
        hover_texts=(
            *((control.rect, tooltip_for(control.icon, tooltips[control.action]))
              for control in controls),
            *(() if shown_as is None else ((layout.app_title, preview_marker.app_title(shown_as)),)),
        ),
    )


def _say_the_name(widget: QWidget, event: object, name: str | None, tooltip) -> None:
    if not name:
        tooltip.hideText()
    else:
        tooltip.showText(widget.mapToGlobal(event.position().toPoint()), name, widget)


def _pointer_at(event: object) -> tuple[int, int]:
    pos = event.position()
    return int(pos.x()), int(pos.y())


class DashboardWidget(QWidget):
    action_triggered = pyqtSignal(str)

    def __init__(self, scene: DashboardScene, parent: QWidget | None = None, *, tooltip=QToolTip) -> None:
        super().__init__(parent)
        self._tooltip = tooltip
        self._hovered: Rect | None = None
        self.setMouseTracking(True)
        self.set_scene(scene)

    def set_scene(self, scene: DashboardScene) -> None:
        self._scene = scene
        self.setFixedSize(scene.width, scene.height)
        self.update()

    def paintEvent(self, event: object) -> None:
        scene = self._scene
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QBrush(BG_PRIMARY))

        for item in scene.rects:
            p.setPen(QPen(item.outline, 1))
            p.setBrush(QBrush(hovered(item.fill) if item.rect == self._hovered
                              else item.fill))
            p.drawRoundedRect(
                QRectF(item.rect.x + 0.5, item.rect.y + 0.5,
                       item.rect.width - 1, item.rect.height - 1),
                _BUTTON_RADIUS, _BUTTON_RADIUS,
            )

        for item in scene.texts:
            p.setPen(QPen(item.color))
            p.setFont(item.font)
            p.drawText(QRectF(item.rect.x, item.rect.y, item.rect.width, item.rect.height),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, item.text)

        for item in scene.images:
            px_x = item.rect.x + (item.rect.width - item.pixmap.width()) // 2
            px_y = item.rect.y + (item.rect.height - item.pixmap.height()) // 2
            p.drawPixmap(px_x, px_y, item.pixmap)

        p.end()

    def mousePressEvent(self, event: object) -> None:
        action = self._scene.action_at(*_pointer_at(event))
        if action is not None:
            self.action_triggered.emit(action)

    def mouseMoveEvent(self, event: object) -> None:
        x, y = _pointer_at(event)
        self._set_hovered(self._scene.control_at(x, y))
        _say_the_name(self, event, self._scene.name_at(x, y), self._tooltip)

    def leaveEvent(self, event: object) -> None:
        self._set_hovered(None)

    def _set_hovered(self, rect: Rect | None) -> None:
        if rect != self._hovered:
            self._hovered = rect
            self.update()


class RfbModeSelectorWidget(PressableDrawing):
    action_triggered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, tooltip=QToolTip) -> None:
        self.buttons = RfbModeSelector(x=PAD, y=PAD, post=self._post)
        super().__init__(self.buttons, parent)
        self._tooltip = tooltip
        self.setFixedHeight(RFB_MODE_SELECTOR_HEIGHT)
        self.show_state(DashboardSnapshot())

    def show_state(self, snapshot: DashboardSnapshot) -> None:
        if self.buttons.show(snapshot.rfb_mode, origenerator_core_ready=snapshot.origenerator_core_ready):
            self.update()

    def drawing(self) -> tuple[bytes, tuple[int, int]]:
        size = self.width(), self.height()
        return self.buttons.rgba(size), size

    def mouseMoveEvent(self, event: object) -> None:
        super().mouseMoveEvent(event)
        _say_the_name(self, event, self.buttons.name_under_the_pointer, self._tooltip)

    def _post(self, command: str) -> None:
        self.action_triggered.emit(command)


class ReferencePopup:
    def __init__(self, parent: QWidget, rfb_rect: Rect | None, *,
                 on_change: Callable[[], None],
                 on_typing_invited: Callable[[], None],
                 actions: Callable[[], Acts]) -> None:
        self._parent = parent
        self._rfb_rect = rfb_rect
        self._on_change = on_change
        self._on_typing_invited = on_typing_invited
        self._actions = actions
        self.dialog: ReferenceDialog | None = None

    @property
    def is_open(self) -> bool:
        return self.dialog is not None and self.dialog.isVisible()

    def toggle(self, omni_paused: bool) -> None:
        if self.is_open:
            self.dialog.close()
        else:
            self.open(omni_paused)

    def open(self, omni_paused: bool) -> None:
        if self.dialog is None:
            self.dialog = ReferenceDialog(self._parent, actions=self._actions())
            self.dialog.finished.connect(lambda _result: self._on_change())
            self.dialog.typing_invited.connect(self._on_typing_invited)
            if self._rfb_rect is not None:
                self._fit_to(self._rfb_rect)
        self.dialog.show()
        bring_to_front(self.dialog)
        # Qt applies the StaysOnTop hint on show, so a band set before this
        # would be undone by the show and the popup would be stranded above a
        # desktop OmniPause had freed.  After, not before.
        self.sync_topmost(omni_paused)
        self._on_change()

    def close(self) -> None:
        if self.dialog is not None:
            self.dialog.close()

    def sync_topmost(self, omni_paused: bool) -> None:
        if self.dialog is None:
            return
        self.dialog.sync_topmost(omni_paused)

    def _fit_to(self, rect: Rect) -> None:
        dialog = self.dialog
        assert dialog is not None
        dialog.setGeometry(rect.x, rect.y, rect.width, rect.height)
        dialog.show()
        frame = dialog.frameGeometry()
        client = dialog.geometry()
        x, y, w, h = client_rect_filling_frame(
            rect,
            left=client.left() - frame.left(),
            upper=client.top() - frame.top(),
            right=frame.right() - client.right(),
            lower=frame.bottomLeft().y() - client.bottomLeft().y(),
        )
        dialog.setGeometry(x, y, w, h)


def write_dashboard_command(path: Path, action_id: str) -> None:
    # PyQt6 aborts the window on an exception left unhandled in a click slot, so this drops rather
    # than raises.
    append_command(path, action_id)


def apply_dashboard_window_geometry(
    window: QWidget,
    scene: DashboardScene,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
) -> None:
    if launch_geometry is not None:
        window.setGeometry(
            launch_geometry.x, launch_geometry.y,
            launch_geometry.width, launch_geometry.height,
        )
        return
    window.resize(scene.width, scene.height)


class DashboardWindow(QMainWindow):
    _press_received = pyqtSignal()

    def __init__(
        self,
        app_config: DashboardAppConfig,
        bar_layout: DashboardBarLayout,
        *,
        launch_geometry: DashboardLaunchGeometry | None = None,
        rfb_rect: Rect | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        self._bar_layout = bar_layout
        self._launch_geometry = launch_geometry
        self._reference = ReferencePopup(self, rfb_rect, on_change=self._repaint_bar,
                                         on_typing_invited=self._pause_for_typing,
                                         actions=partial(published_actions_heard_as, app_config.state_dir))
        self._reveal = LoadingReveal(app_config.state_dir)

        self._flash = PressFlash(clock=clock)
        self._flash_going_out = self._timer_calling(self._show_presses)
        self._flash_going_out.setSingleShot(True)
        self._last_snapshot: DashboardSnapshot | None = None

        self._shown_as = preview_marker.shown_as()
        self.setWindowTitle(preview_marker.app_title(self._shown_as))
        self.setWindowIcon(app_icon(PROJECT_ICON, self._shown_as))
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self._icons = IconCache()
        self._widget = DashboardWidget(self._bar_scene(frozenset()))
        self._widget.action_triggered.connect(self._on_action)
        self._log_widget = LogPanelWidget.for_session(app_config.state_dir)
        column = QVBoxLayout()
        column.setSpacing(0)
        column.addWidget(self._widget, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self._log_widget, 1)
        self._mode_selector = RfbModeSelectorWidget()
        self._mode_selector.action_triggered.connect(self._on_action)
        column.addWidget(self._mode_selector)
        self._osr2 = Osr2PartWidget(app_config.state_dir)
        self._osr2.action_triggered.connect(
            partial(write_dashboard_command, app_config.dashboard_cmd_file))
        central = QWidget(self)
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addLayout(column, 1)
        central_layout.addWidget(self._osr2, 0, Qt.AlignmentFlag.AlignTop)
        self.setCentralWidget(central)

        if launch_geometry is not None:
            self.setGeometry(
                launch_geometry.x, launch_geometry.y,
                launch_geometry.width, launch_geometry.height,
            )

        _hwnd = int(self.winId())
        self._dash_hwnd = _hwnd
        preview_marker.dress_the_window(_hwnd)
        self._reveal.attach(_hwnd, self)
        set_taskbar_window_styles(_hwnd)

        self._state_dir = app_config.state_dir
        self._ahk_cmd_file = app_config.state_dir / "ahk_cmd.txt"

        self._press_received.connect(self._handle_press_event)
        self._press_channel = LoopbackInbox(
            app_config.state_dir / PRESS_PORT_FILENAME, listener_name="press-listener",
            on_arrival=self._press_received.emit)

        self._messages = MessageFeed(
            app_config.state_dir,
            screens_if_known(app_config.layout),
            make_overlay=MessageOverlay,
            held=self._reveal.deferred,
            list_what_was_heard=self._log_widget.show_what_was_heard,
        )
        self._timer_calling(self._messages.poll).start(FLASH_POLL_MS)
        self._timer_calling(self._refresh).start(BAR_REFRESH_MS)
        self._refresh()

    def _timer_calling(self, slot: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.timeout.connect(slot)
        return timer

    def _post_to_the_dispatch_loop(self, action: str) -> None:
        write_dashboard_command(self._app_config.dashboard_cmd_file, action)

    def closeEvent(self, event: object) -> None:
        mark_session_end(self._state_dir, "the dashboard window was closed")
        with contextlib.suppress(OSError):
            self._ahk_cmd_file.write_text("end_session", encoding="utf-8")
        self._stop_background_work()
        event.accept()

    def _stop_background_work(self) -> None:
        self._press_channel.stop()
        for timer in self.findChildren(QTimer):
            timer.stop()
        self._log_widget.shutdown()
        self._osr2.shutdown()
        self._messages.shutdown()

    def changeEvent(self, event: object) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            now_minimized = self.isMinimized()
            was_minimized = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            self._maybe_route_omniminimize(now_minimized=now_minimized, was_minimized=was_minimized)
            self._maybe_route_omnirestore(now_minimized=now_minimized, was_minimized=was_minimized)
        super().changeEvent(event)

    def _maybe_route_omniminimize(self, *, now_minimized: bool, was_minimized: bool) -> None:
        if self._reveal.routing_suppressed:
            return
        if now_minimized and not was_minimized:
            self._post_to_the_dispatch_loop(OMNIMINIMIZE)

    def _maybe_route_omnirestore(self, *, now_minimized: bool, was_minimized: bool) -> None:
        if not (was_minimized and not now_minimized):
            return
        if self._reveal.took_the_reveals_own_restore():
            return
        self._post_to_the_dispatch_loop(OMNIRESTORE)

    def _show_presses(self) -> None:
        self._do_render(self._last_snapshot, self._flash.lit())
        waiting = self._flash.seconds_until_one_goes_out()
        if waiting is not None:
            self._flash_going_out.start(ceil(waiting * 1000))

    def _sync_own_topmost(self, omni_paused: bool) -> None:
        keep_in_topmost_band(self._dash_hwnd, topmost=not omni_paused)

    @property
    def _omni_paused(self) -> bool:
        return self._last_snapshot is not None and self._last_snapshot.omni_paused

    def _do_render(
        self,
        snapshot: DashboardSnapshot | None,
        pressed_actions: frozenset[str],
    ) -> None:
        self._last_snapshot = snapshot
        self._mode_selector.show_state(snapshot or DashboardSnapshot())
        omni_paused = self._omni_paused
        self._sync_own_topmost(omni_paused)
        self._reference.sync_topmost(omni_paused)
        scene = self._bar_scene(pressed_actions)
        if not self._placing_it_now_would_show_or_restore_it():
            apply_dashboard_window_geometry(self, scene, launch_geometry=self._launch_geometry)
        self._widget.set_scene(scene)

    def _placing_it_now_would_show_or_restore_it(self) -> bool:
        return self.isMinimized() or self._reveal.deferred

    def _bar_scene(self, pressed_actions: frozenset[str]) -> DashboardScene:
        return build_dashboard_scene(
            self._bar_layout,
            BarState.of(self._last_snapshot, self._reference.is_open),
            icons=self._icons,
            pressed_actions=pressed_actions,
            shown_as=self._shown_as,
        )

    def _repaint_bar(self) -> None:
        self._widget.set_scene(self._bar_scene(self._flash.lit()))

    def _pause_for_typing(self) -> None:
        self._post_to_the_dispatch_loop(ENTER_OMNIPAUSE)

    def _on_action(self, action_id: str) -> None:
        if action_id == HELP_REFERENCE:
            self._reference.toggle(self._omni_paused)
            return
        if action_id == QUIT_BUTTON and not sure(self, QUIT_TITLE, QUIT_QUESTION):
            return
        self._flash.press(action_id)
        self._post_to_the_dispatch_loop(action_id)
        self._show_presses()

    def _handle_press_event(self) -> None:
        self._apply_presses(self._press_channel.take_all())

    def _apply_presses(self, actions: list[str]) -> None:
        for action in actions:
            self._flash.press(action)
        if HELP_REFERENCE_CLOSE in actions:
            self._reference.close()
        if HELP_REFERENCE in actions:
            self._reference.toggle(self._omni_paused)
        self._show_presses()

    def _refresh(self) -> None:
        self._reveal.maybe_reveal()
        self._do_render(
            load_dashboard_snapshot(self._app_config.dashboard_state_file),
            self._flash.lit(),
        )


IMPORTED_CHECKOUT_FILENAME = "dashboard_source.txt"


def imported_checkout() -> Path:
    return PROJECT_DIR


def record_imported_checkout(state_dir: Path) -> Path:
    destination = Path(state_dir) / IMPORTED_CHECKOUT_FILENAME
    with contextlib.suppress(OSError):
        destination.write_text(f"{imported_checkout()}\n", encoding="utf-8")
    return destination


def build_dashboard_window(
    app_config: DashboardAppConfig,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
    rfb_rect: Rect | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DashboardWindow:
    return DashboardWindow(
        app_config, compute_dashboard_bar_layout(),
        launch_geometry=launch_geometry,
        rfb_rect=rfb_rect,
        clock=clock,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Fun Time dashboard preview app")
    parser.add_argument(
        "manifest_path",
        nargs="?",
        default=str(Path("state") / WINDOWS_BRIDGE_MANIFEST_FILENAME),
        help="Path to the Windows bridge launch manifest",
    )
    add_rect_arguments(parser)
    add_rect_arguments(parser, prefix="rfb_")
    return parser.parse_args(argv)


def set_up_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    install_exception_logging(logger)
    return logger


def claim_taskbar_identity(identity: str, claim: Callable[[str], None]) -> None:
    try:
        claim(identity)
    except OSError:
        logger.warning("Windows refused the taskbar identity %s; grouping will not work", identity)


def main(argv: list[str] | None = None) -> int:
    set_up_logging()
    args = parse_args(argv)


    claim_taskbar_identity(preview_marker.session_identity(preview_marker.shown_as()),
                           set_app_user_model_id)

    with application([]) as app:
        dress_application(app)

        app_config = load_dashboard_app_config(Path(args.manifest_path))
        record_imported_checkout(app_config.state_dir)
        launch_geometry = rect_from_arguments(args)
        rfb_rect = rect_from_arguments(args, prefix="rfb_")
        _window = build_dashboard_window(
            app_config,
            launch_geometry=launch_geometry,
            rfb_rect=rfb_rect,
        )
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

