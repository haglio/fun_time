from __future__ import annotations

import argparse
import configparser
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from math import ceil
from pathlib import Path

from app_support.logging_utils import install_exception_logging
from app_support.win32 import set_app_user_model_id
from funestra.file_channel import append_command
from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QTextBrowser,
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
from shared_ui.find_bar import FindBar
from shared_ui.fonts import FONT_UI, SIZE_BODY
from shared_ui.foreground import bring_to_front
from shared_ui.icon_geometry import tooltip_for
from shared_ui.icons import icon_pixmap
from shared_ui.pressable_drawing import PressableDrawing
from shared_ui.preview import Preview
from shared_ui.preview_icon import app_icon
from shared_ui.spacing import BUTTON_RADIUS_HUD, MARGIN_STANDARD

from fun_time import preview_marker
from fun_time.command_reference import render_reference_html
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
    RESET_ALL,
    VOICE_TOGGLE,
)
from fun_time.dashboard_bridge import DashboardSnapshot
from fun_time.dashboard_controls import BarControl, bar_controls, icon_side
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
from fun_time.loading_reveal import LoadingReveal
from fun_time.log_panel import LogPanelWidget
from fun_time.loopback_inbox import PRESS_PORT_FILENAME, LoopbackInbox
from fun_time.manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.message_feed import MessageFeed
from fun_time.message_overlay import MessageOverlay
from fun_time.press_flash import PressFlash
from fun_time.project_paths import PROJECT_ICON
from fun_time.rfb_mode_selector import RfbModeSelector
from fun_time.session_end import mark_session_end
from fun_time.shared_state import shared_state_path
from fun_time.win32 import keep_in_topmost_band, set_taskbar_window_styles

logger = logging.getLogger(__name__)

COLOR_BG = BG_PRIMARY
# The family's own resting button ground -- the one Origenerator's toolbar
# buttons sit on, so these read as the same raised object that app's do.
COLOR_PANEL = BG_BUTTON
COLOR_TEXT = TEXT_PRIMARY
# A hue of its own, deliberately not the family icon's ink.


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
    # The session's state directory -- the manifest's own, and where every
    # file the dashboard reads or writes lives.  Named once here: derived
    # at each use from two different fields, one of which could fall back
    # relative, half the file IPC once went to the working directory.
    state_dir: Path
    dashboard_state_file: Path
    dashboard_cmd_file: Path
    # Whether the session hosts an Origenerator, which is what puts the strip
    # under the log that says what holds the Random Favs Browser's place.
    rfb_mode_selector: bool = False


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
        # The manifest writes both absolute; a bare name lands in the state dir.
        dashboard_state_file=state_dir / parser.get(
            "commands", "dashboard_state_file", fallback="dashboard_state.ini"),
        dashboard_cmd_file=state_dir / parser.get(
            "commands", "dashboard_cmd_file", fallback="dashboard_cmd.txt"),
        rfb_mode_selector=bool(parser.get("runtime", "origenerator_dir", fallback="").strip()),
    )


class IconCache:
    """The pixmaps one bar is painted from, kept for as long as that bar: every
    render asks for the same handful, twice a second."""

    def __init__(self) -> None:
        self._icons: dict[tuple[str, int], QPixmap] = {}
        self._family_icons: dict[tuple[str, int, int], QPixmap] = {}

    def icon(self, path: Path, height: int) -> QPixmap:
        """An .ico scaled to a square of *height* pixels."""
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
        """One of the family's icons, drawn square for the control it sits in.

        From shared_ui, not typed as a font character: typed, each icon came out
        at whatever weight its face gave it.  Square, because a icon drawn to a
        wide panel's aspect stops being round; the widget centers it.

        *color* is the bar's ink unless the icon owns one — F-mode's is magenta.
        """
        ink = color or COLOR_TEXT
        key = (name, rect.width, rect.height, ink.rgba())
        if key not in self._family_icons:
            self._family_icons[key] = icon_pixmap(name, icon_side(rect), ink)
        return self._family_icons[key]


# The reference popup's name, on its window chrome and on the ? button's tooltip
# — one constant so the two can't drift, and so tests can find the real window.
REFERENCE_WINDOW_TITLE = "Hotkeys & Voice Commands Reference"

_BUTTON_RADIUS = BUTTON_RADIUS_HUD

# Every control in the bar names itself on hover.  Omnipause names the act the
# press takes rather than the state it is in, as its icon does.
_ACTION_TOOLTIPS: dict[str, str] = {
    QUIT_BUTTON: "Quit",
    OMNIPAUSE_TOGGLE: "Pause everything",
    HELP_REFERENCE: REFERENCE_WINDOW_TITLE,
    VOICE_TOGGLE: "Voice",
    FMODE_TOGGLE: "F-Mode on every Funestra",
    RESET_ALL: ("Reset every Funestra — no filter, no lock, no loop, no F-Mode, "
                "normal speed, shuffled from the top"),
    ENTER_VR: "Enter VR — end this session and open FunTimeVR",
    EXIT_VR: "Exit VR — end this session and open Fun Time on the desktop",
}
OMNIPAUSE_RESUME_TOOLTIP = "Play everything"


def build_dashboard_scene(
    layout: DashboardBarLayout,
    snapshot: DashboardSnapshot | None = None,
    *,
    icons: IconCache,
    pressed_actions: frozenset[str] = frozenset(),
    reference_open: bool = False,
    shown_as: Preview | None = None,
) -> DashboardScene:
    """The control bar: the app's icon, the session's four, then the three that
    reach past it.  Nothing here stands for one Funestra."""
    omni_paused = snapshot is not None and snapshot.omni_paused
    controls = bar_controls(
        layout,
        omni_paused=omni_paused,
        voice_active=snapshot is not None and snapshot.voice_active,
        f_mode=snapshot is not None and snapshot.f_mode,
        in_vr=snapshot is not None and snapshot.in_vr,
        reference_open=reference_open,
        nothing_to_reset=snapshot is not None and snapshot.nothing_to_reset,
        headset_ready=snapshot is None or snapshot.headset_ready,
    )

    def _rect(control: BarControl) -> DashboardRectItem:
        """Lighter while it is being pressed -- a control already wearing a
        state color lightens THAT, so a pressed mic stays blue."""
        if control.lit is None:
            fill, outline = COLOR_PANEL, TEXT_MUTED
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

    # Only the app's own name is set in type now; every control wears a drawn
    # icon, so the bar carries one weight across it.
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


def _pointer_at(event: object) -> tuple[int, int]:
    pos = event.position()
    return int(pos.x()), int(pos.y())


class DashboardWidget(QWidget):
    """Custom widget that paints a DashboardScene using QPainter."""

    action_triggered = pyqtSignal(str)

    def __init__(self, scene: DashboardScene, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Which control the pointer is over, so it can be drawn one step lighter.
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
        p.fillRect(self.rect(), QBrush(COLOR_BG))

        for item in scene.rects:
            # Rounded, on a subtle edge: the shape Origenerator's toolbar buttons
            # have.  Square-cornered they read as panels rather than buttons.
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
            # Center the square pixmap within the (possibly wider) rect
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
        name = self._scene.name_at(x, y)
        if name is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(self.mapToGlobal(event.position().toPoint()), name, self)

    def leaveEvent(self, event: object) -> None:
        self._set_hovered(None)

    def _set_hovered(self, rect: Rect | None) -> None:
        """Remember which control the pointer is on, repainting when it moves.
        Only on a change: a repaint per pixel crossed is a repaint for nothing.
        """
        if rect != self._hovered:
            self._hovered = rect
            self.update()


class RfbModeSelectorWidget(PressableDrawing):
    """The mode selector under the log, hovered and pressed the way a Satellite
    Funestra's HUD is (:mod:`fun_time.rfb_mode_selector`)."""

    action_triggered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        self.buttons = RfbModeSelector(x=PAD, y=PAD, post=self._post)
        super().__init__(self.buttons, parent)
        self.setFixedHeight(RFB_MODE_SELECTOR_HEIGHT)
        self.show_state(DashboardSnapshot())

    def show_state(self, snapshot: DashboardSnapshot) -> None:
        if self.buttons.show(snapshot.rfb_mode, origenerator_ready=snapshot.origenerator_ready):
            self.update()

    def drawing(self) -> tuple[bytes, tuple[int, int]]:
        size = self.width(), self.height()
        return self.buttons.rgba(size), size

    def mouseMoveEvent(self, event: object) -> None:
        super().mouseMoveEvent(event)
        name = self.buttons.name_under_the_pointer
        if name is None:
            QToolTip.hideText()
        else:
            QToolTip.showText(self.mapToGlobal(event.position().toPoint()), name, self)

    def _post(self, command: str) -> None:
        self.action_triggered.emit(command)


class ReferenceDialog(QDialog):
    """Modeless popup listing every hotkey and voice command.

    Carries no in-window heading — its content title lives on the window chrome
    ("Hotkeys & Voice Commands Reference") — and is sized/placed by the caller to fill the
    Random Favs Browser's rect.
    """

    typing_invited = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(REFERENCE_WINDOW_TITLE)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowIcon(app_icon(PROJECT_ICON, preview_marker.shown_as()))
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        self._browser.setHtml(render_reference_html())
        self._find_bar = FindBar("Find:", "a key or a word…")
        self._find_bar.setContentsMargins(MARGIN_STANDARD, 0, MARGIN_STANDARD, MARGIN_STANDARD)
        self._find_bar.typing_invited.connect(self.typing_invited)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._browser)
        layout.addWidget(self._find_bar)
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self.open_find)

    def open_find(self) -> None:
        self._find_bar.open_find([self._browser])

    def sync_topmost(self, omni_paused: bool) -> None:
        """Follow the same OmniPause band policy as every other Fun Time window.

        This popup floats over the Funestras via WindowStaysOnTopHint, but it is
        not one of the bridge's MANAGED_WINDOWS — it comes and goes with the ``?``
        button — so the orchestrator's disable_all_topmost never reached it and
        it stayed stranded above a freed desktop for the whole pause.  It is its
        own top-level window, so the dashboard's band does not carry it either;
        it corrects its own, the same way DashboardWindow._sync_own_topmost does.

        winId() is read fresh rather than cached: Qt may recreate the native
        window across a hide/show, and every toggle hides and shows this one.
        """
        keep_in_topmost_band(int(self.winId()), topmost=not omni_paused)


class ReferencePopup:
    """The reference window's whole life: opened, dismissed, placed, banded.

    Lazily built, so a session that never asks for it never pays for rendering
    the reference HTML; and kept once built, so a second open lands where the
    user left it.
    """

    def __init__(self, parent: QWidget, rfb_rect: Rect | None, *,
                 on_change: Callable[[], None],
                 on_typing_invited: Callable[[], None]) -> None:
        self._parent = parent
        self._rfb_rect = rfb_rect
        self._on_change = on_change
        self._on_typing_invited = on_typing_invited
        self.dialog: ReferenceDialog | None = None

    @property
    def is_open(self) -> bool:
        return self.dialog is not None and self.dialog.isVisible()

    def toggle(self, omni_paused: bool) -> None:
        """Open it, or dismiss it if it is already showing.

        Drives both the ``?`` button and the "help"/"reference"/… phrases: one
        trigger both ways.
        """
        if self.is_open:
            self.dialog.close()
        else:
            self.open(omni_paused)

    def open(self, omni_paused: bool) -> None:
        """Show it, or re-focus it if it is already open."""
        if self.dialog is None:
            self.dialog = ReferenceDialog(self._parent)
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
        """Dismiss it if it is open (the "close …" phrases)."""
        if self.dialog is not None:
            self.dialog.close()

    def sync_topmost(self, omni_paused: bool) -> None:
        """Keep its band in step with OmniPause, open or not.

        It is a separate top-level window, so it rides neither the panel's band
        nor the orchestrator's drop.  Run while hidden too, so a re-open lands
        in the right band.
        """
        if self.dialog is None:
            return
        self.dialog.sync_topmost(omni_paused)

    def _fit_to(self, rect: Rect) -> None:
        """Size it so its whole FRAME — title bar included — fills *rect*.

        Frame margins are known only once the window is realized, so: place it
        at the rect, show it, measure, then inset the client to fill the frame.
        Sizing the client area instead left the chrome overhanging the top.
        """
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
    """Post a dashboard button (or voice-toggle) action for the dispatch loop.

    Retries past the loop's ~20 Hz rename-drain of the same file and DROPS
    rather than raising: unhandled, that error leaves a click slot and PyQt6
    aborts the window — the "power button closed the Dash instead of quitting
    Fun Time" bug.  Three cases in `tests/test_dashboard_app.py` hold it.
    """
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
    """Main dashboard window — PyQt6 equivalent of the old build_dashboard_window()."""

    _press_received = pyqtSignal()

    def __init__(
        self,
        app_config: DashboardAppConfig,
        bar_layout: DashboardBarLayout,
        *,
        launch_geometry: DashboardLaunchGeometry | None = None,
        rfb_rect: Rect | None = None,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        self._bar_layout = bar_layout
        self._launch_geometry = launch_geometry
        # The reference popup opens over the Random Favs Browser's screen rect.
        self._reference = ReferencePopup(self, rfb_rect, on_change=self._repaint_bar,
                                         on_typing_invited=self._pause_for_typing)
        # Read once; the message feed below is seeded from this same answer.
        self._reveal = LoadingReveal(app_config.state_dir)

        self._flash = PressFlash()
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
        self._mode_selector = RfbModeSelectorWidget() if app_config.rfb_mode_selector else None
        if self._mode_selector is not None:
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

        # What the title bar's two surviving controls route to: close through
        # closeEvent (quits everything), minimize through changeEvent
        # (omniminimize).  winId() realizes the native handle WITHOUT showing
        # the window, so while the loading screen is up this one stays fully hidden and
        # LoadingReveal shows it once the loading screen goes.
        _hwnd = int(self.winId())
        self._dash_hwnd = _hwnd
        preview_marker.dress_the_window(_hwnd)
        self._reveal.attach(_hwnd, self)
        set_taskbar_window_styles(_hwnd)

        self._state_dir = app_config.state_dir
        self._ahk_cmd_file = app_config.state_dir / "ahk_cmd.txt"

        # Connected first: the channel's listener emits as soon as it exists.
        self._press_received.connect(self._handle_press_event)
        self._press_channel = LoopbackInbox(
            app_config.state_dir / PRESS_PORT_FILENAME, listener_name="press-listener",
            on_arrival=self._press_received.emit)

        self._messages = MessageFeed(
            layout=app_config.layout,
            rfb_mode_selector=app_config.rfb_mode_selector,
            event_log_dir=app_config.state_dir,
            loading_screen_dir=app_config.state_dir,
            make_overlay=MessageOverlay,
            held=self._reveal.deferred,
            list_what_was_heard=self._log_widget.show_what_was_heard,
            shared_state_file=shared_state_path(app_config.state_dir),
        )
        self._timer_calling(self._messages.poll).start(250)
        self._timer_calling(self._refresh).start(500)
        self._refresh()

    def _timer_calling(self, slot: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.timeout.connect(slot)
        return timer

    def _post_command(self, action: str) -> None:
        write_dashboard_command(self._app_config.dashboard_cmd_file, action)

    def closeEvent(self, event: object) -> None:
        # Closing this window ends the WHOLE session; a stray Alt+F4 lands here.
        mark_session_end(self._state_dir, "the dashboard window was closed")
        try:
            self._ahk_cmd_file.write_text("end_session", encoding="utf-8")
        except OSError:
            pass
        self._stop_background_work()
        event.accept()

    def _stop_background_work(self) -> None:
        """Wind down the press channel, the timers and the log strip's tail.

        In production the process is about to be killed anyway; this matters
        where several dashboards are built and closed inside one longer-lived
        process, and a leaked poller would read on past its window.
        """
        self._press_channel.stop()
        for timer in self.findChildren(QTimer):
            timer.stop()
        self._log_widget.shutdown()
        self._osr2.shutdown()
        self._messages.shutdown()

    def changeEvent(self, event: object) -> None:
        """Mirror the dashboard's own minimize/restore onto every managed window.

        The dashboard cannot reach the other processes' windows directly, so it
        writes a command for the dispatch loop, which owns those handles.  This
        is what makes clicking the taskbar icon (which restores the dashboard)
        bring every window back.
        """
        if event.type() == QEvent.Type.WindowStateChange:
            now_minimized = self.isMinimized()
            was_minimized = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            self._maybe_route_omniminimize(now_minimized=now_minimized, was_minimized=was_minimized)
            self._maybe_route_omnirestore(now_minimized=now_minimized, was_minimized=was_minimized)
        super().changeEvent(event)

    def _maybe_route_omniminimize(self, *, now_minimized: bool, was_minimized: bool) -> None:
        """Write the omniminimize command on the not-minimized -> minimized edge only."""
        if self._reveal.routing_suppressed:
            return  # startup minimize (loading overlay) — not a user gesture
        if now_minimized and not was_minimized:
            self._post_command(OMNIMINIMIZE)

    def _maybe_route_omnirestore(self, *, now_minimized: bool, was_minimized: bool) -> None:
        """Write the omnirestore command on the minimized -> not-minimized edge only."""
        if not (was_minimized and not now_minimized):
            return
        if self._reveal.took_the_first_restore():
            return  # startup's own minimize came back; routing is live from here
        self._post_command(OMNIRESTORE)

    def _show_presses(self) -> None:
        self._do_render(self._last_snapshot, self._flash.lit())
        waiting = self._flash.seconds_until_one_goes_out()
        if waiting is not None:
            self._flash_going_out.start(ceil(waiting * 1000))

    def _sync_own_topmost(self, omni_paused: bool) -> None:
        """Keep the dashboard's own topmost band in step with OmniPause.

        The orchestrator tries to drop it, but its lookup for this Qt window
        (whose pid differs from the launcher's) intermittently fails, so the
        panel corrects its own band from its reliable handle.
        """
        keep_in_topmost_band(self._dash_hwnd, topmost=not omni_paused)

    @property
    def _omni_paused(self) -> bool:
        """Whether the last snapshot we rendered had OmniPause holding."""
        return self._last_snapshot is not None and self._last_snapshot.omni_paused

    def _do_render(
        self,
        snapshot: DashboardSnapshot | None,
        pressed_actions: frozenset[str],
    ) -> None:
        self._last_snapshot = snapshot
        if self._mode_selector is not None:
            self._mode_selector.show_state(snapshot or DashboardSnapshot())
        # The log strip is a child widget and rides this window's band; the
        # reference popup is its own top-level window, so it is banded too.
        omni_paused = self._omni_paused
        self._sync_own_topmost(omni_paused)
        self._reference.sync_topmost(omni_paused)
        scene = self._bar_scene(pressed_actions)
        # While minimized, re-asserting geometry would restore the window and
        # fight the omniminimize — leave it minimized until the user restores it.
        # While deferred for loading it is hidden; don't touch it until reveal.
        if not self.isMinimized() and not self._reveal.deferred:
            apply_dashboard_window_geometry(self, scene, launch_geometry=self._launch_geometry)
        self._widget.set_scene(scene)

    def _bar_scene(self, pressed_actions: frozenset[str]) -> DashboardScene:
        return build_dashboard_scene(
            self._bar_layout,
            self._last_snapshot,
            icons=self._icons,
            pressed_actions=pressed_actions,
            reference_open=self._reference.is_open,
            shown_as=self._shown_as,
        )

    def _repaint_bar(self) -> None:
        self._widget.set_scene(self._bar_scene(self._flash.lit()))

    def _pause_for_typing(self) -> None:
        self._post_command(ENTER_OMNIPAUSE)

    def _on_action(self, action_id: str) -> None:
        if action_id == HELP_REFERENCE:
            self._reference.toggle(self._omni_paused)
            return
        self._flash.press(action_id)
        self._post_command(action_id)
        self._show_presses()

    def _handle_press_event(self) -> None:
        self._apply_presses(self._press_channel.take_all())

    def _apply_presses(self, actions: list[str]) -> None:
        """Flash each of *actions*, and route the two the popup answers to.

        Voice arrives here as a press (the ? button drives _on_action directly):
        "help"/… toggles the popup, "close help"/… only dismisses it.  A burst
        is one render, not one render each, so the two are decided across the
        whole batch before either is acted on.
        """
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


# Which checkout this process actually imported, written beside the session's
# other state.  A branch session runs from a worktree by setting the working
# directory, and nothing anywhere said whether that had taken -- so a change that
# was in the code and not on the screen left no way to tell the two apart, and
# the guessing that follows costs a review round every time.
SOURCE_CHECKOUT_FILENAME = "dashboard_source.txt"


def source_checkout() -> Path:
    """The checkout this module was imported from."""
    return Path(__file__).resolve().parent.parent


def record_source_checkout(state_dir: Path) -> Path:
    """Write :func:`source_checkout` where the session's state lives."""
    destination = Path(state_dir) / SOURCE_CHECKOUT_FILENAME
    try:
        destination.write_text(f"{source_checkout()}\n", encoding="utf-8")
    except OSError:
        pass  # A dashboard that cannot leave a note still runs.
    return destination


def build_dashboard_window(
    app_config: DashboardAppConfig,
    *,
    launch_geometry: DashboardLaunchGeometry | None = None,
    rfb_rect: Rect | None = None,
) -> DashboardWindow:
    return DashboardWindow(
        app_config, compute_dashboard_bar_layout(),
        launch_geometry=launch_geometry,
        rfb_rect=rfb_rect,
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
    # The Random Favs Browser's rect — the reference popup opens over it.
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

    # Set AppUserModelID before any window creation so the taskbar can group
    # this process's windows with the pinned "Fun Time" shortcut.

    claim_taskbar_identity(preview_marker.session_identity(preview_marker.shown_as()),
                           set_app_user_model_id)

    with application([]) as app:
        # The family's chrome, on the application: a tooltip is a top-level popup,
        # and only a sheet set here reaches it.
        dress_application(app)

        app_config = load_dashboard_app_config(Path(args.manifest_path))
        record_source_checkout(app_config.state_dir)
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

