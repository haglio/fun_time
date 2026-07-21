from __future__ import annotations

import argparse
import configparser
import ctypes
from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path
import queue
import socket
import threading
import time

from PyQt6.QtGui import QColor, QFont
from player_core.file_channel import append_command

from shared_ui.colors import (
    BG_PRIMARY,
    BG_SECONDARY,
    BLUE,
    BORDER_PANEL,
    GREEN,
    TEXT_PRIMARY,
)
from shared_ui.fonts import FONT_SYMBOL, FONT_UI, SIZE_BODY, SIZE_SMALL, make_font

from fun_time.config import LayoutConfig
from fun_time.startup_progress import loading_screen_active
from fun_time.manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME
from fun_time.win32 import is_window_topmost, set_always_on_top
from fun_time.dashboard_actions import (
    FMODE_PANEL,
    HELP_REFERENCE,
    HELP_REFERENCE_CLOSE,
    OMNIMINIMIZE,
    OMNIRESTORE,
    OMNIPAUSE_TOGGLE,
    QUIT_BUTTON,
    VOICE_TOGGLE,
)
from fun_time.command_reference import render_reference_html
from fun_time.event_log import EVENT_LOG_FILENAME, event_log_path, read_events
from fun_time.log_panel import LogPanelWidget, prefs_path
from fun_time.monitors import enumerate_monitors, get_logical_monitor_rects
from fun_time.notice_overlay import (
    NoticeOverlay,
    PlayerRects,
    is_announcement,
    notice_target_rect,
)
from fun_time.window_layout import compute_primary_media_rect, compute_window_layout
from fun_time.dashboard_layout import (
    PAD as BAR_PAD,
    DashboardBarLayout,
    Rect,
    client_rect_filling_frame,
    compute_dashboard_bar_layout,
)
from fun_time.dashboard_runtime import DashboardSnapshot, load_dashboard_snapshot

COLOR_BG = BG_PRIMARY
COLOR_PANEL = BG_SECONDARY
COLOR_TEXT = TEXT_PRIMARY
# The "Fun Time" wordmark matches the loading screen's redder pink text, NOT the
# logo's magenta-pink — they are deliberately different hues.
COLOR_APP_TITLE = QColor("#e94560")


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
    dashboard_state_file: Path
    dashboard_cmd_file: Path


@dataclass(frozen=True)
class DashboardLaunchGeometry:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class DashboardTextItem:
    text: str
    rect: Rect
    color: QColor = field(default_factory=lambda: COLOR_TEXT)
    anchor: str = "center"
    font: QFont | None = None


@dataclass(frozen=True)
class DashboardImageItem:
    pixmap: QPixmap
    rect: Rect


@dataclass(frozen=True)
class DashboardRectItem:
    rect: Rect
    outline: QColor = field(default_factory=lambda: BORDER_PANEL)
    fill: QColor = field(default_factory=lambda: COLOR_PANEL)


@dataclass(frozen=True)
class DashboardScene:
    width: int
    height: int
    rects: tuple[DashboardRectItem, ...]
    texts: tuple[DashboardTextItem, ...]
    actions: tuple[tuple[str, Rect], ...]
    hover_texts: tuple[tuple[Rect, str], ...] = ()
    images: tuple[DashboardImageItem, ...] = ()


def load_dashboard_app_config(manifest_path: Path) -> DashboardAppConfig:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(manifest_path, encoding="utf-8")

    layout = LayoutConfig(
        main_monitor=parser.getint("layout", "main_monitor"),
        secondary_monitor=parser.getint("layout", "secondary_monitor"),
        primary_top_ratio=parser.getfloat("layout", "primary_top_ratio"),
        landscape_width_ratio=parser.getfloat("layout", "landscape_width_ratio"),
    )
    return DashboardAppConfig(
        layout=layout,
        manifest_path=manifest_path,
        dashboard_state_file=Path(parser.get("commands", "dashboard_state_file", fallback="dashboard_state.ini")),
        dashboard_cmd_file=Path(parser.get("commands", "dashboard_cmd_file", fallback="dashboard_cmd.txt")),
    )


_dashboard_pixmap_cache: dict[tuple[str, int], QPixmap] = {}


def _load_icon_pixmap(filename: str, height: int) -> QPixmap:
    """Load an icon .ico scaled to a square of *height* pixels, cached."""
    key = (filename, height)
    if key not in _dashboard_pixmap_cache:
        from PyQt6.QtCore import Qt

        ico_path = Path(__file__).resolve().parent.parent / filename
        pm = QPixmap(str(ico_path))
        if not pm.isNull():
            pm = pm.scaled(
                height, height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        _dashboard_pixmap_cache[key] = pm
    return _dashboard_pixmap_cache[key]


def _draw_mic_pixmap(w: int, h: int) -> QPixmap:
    """Draw the familiar voice-input microphone as a QPixmap, cached.

    A capsule head cradled by an upward-opening arc over a short stem and base —
    the mic glyph recording apps use — which reads as "voice" where a bare
    letter or a karaoke-mic emoji did not.  Drawn in a square centred in the
    panel so it stays round whatever the panel's aspect.
    """
    key = ("mic", w, h)
    if key not in _dashboard_pixmap_cache:
        from PyQt6.QtCore import Qt

        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(w, h)
        oy = (h - s) / 2.0
        cx = w / 2.0
        pen = QPen(COLOR_TEXT, max(1, round(s * 0.09)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Capsule (mic head): a filled stadium in the upper portion.
        cap_w = s * 0.36
        painter.setBrush(QBrush(COLOR_TEXT))
        painter.drawRoundedRect(
            round(cx - cap_w / 2), round(oy + s * 0.12),
            round(cap_w), round(s * 0.44), cap_w / 2, cap_w / 2,
        )
        # Cradle: an upward-opening arc cupping the capsule from below.
        painter.setBrush(Qt.GlobalColor.transparent)
        r = round(s * 0.30)
        arc_cy = round(oy + s * 0.40)
        painter.drawArc(round(cx) - r, arc_cy - r, 2 * r, 2 * r, 200 * 16, 140 * 16)
        # Stem down to a short base line.
        base_y = round(oy + s * 0.90)
        painter.drawLine(round(cx), arc_cy + r, round(cx), base_y)
        painter.drawLine(round(cx - s * 0.17), base_y, round(cx + s * 0.17), base_y)
        painter.end()
        _dashboard_pixmap_cache[key] = pm
    return _dashboard_pixmap_cache[key]


# Every control in the bar names itself on hover.
_ACTION_TOOLTIPS: dict[str, str] = {
    QUIT_BUTTON: "Quit",
    OMNIPAUSE_TOGGLE: "Pause everything",
    HELP_REFERENCE: "Hotkeys & Voice Commands Reference",
    FMODE_PANEL: "F-Mode",
    VOICE_TOGGLE: "Voice",
}


def build_dashboard_scene(
    layout: DashboardBarLayout,
    snapshot: DashboardSnapshot | None = None,
    *,
    width: int,
    pressed_actions: frozenset[str] = frozenset(),
) -> DashboardScene:
    """The control bar: the app's mark, the three buttons, the two lights.

    What each player is doing is on that player's own HUD now, so nothing here
    stands for a player — which is why the bar has no shape to keep and simply
    runs along the top of the window.  The OSR2 broker light went to the primary's
    HUD with the rest of the device status; it is the primary's, not the room's.
    """
    fmode_fill = GREEN if snapshot is not None and snapshot.f_mode_enabled else COLOR_PANEL
    voice_fill = BLUE if snapshot is not None and snapshot.voice_active else COLOR_PANEL

    omni_paused = snapshot is not None and snapshot.omni_paused
    omnipause_icon = "\u25B6" if omni_paused else "\u23F8"

    def _press_fill(fill: QColor, action_id: str) -> QColor:
        return lighten_color(fill) if action_id in pressed_actions else fill

    rects = (
        DashboardRectItem(layout.quit_button, fill=_press_fill(COLOR_PANEL, QUIT_BUTTON)),
        DashboardRectItem(layout.omnipause_button, fill=_press_fill(COLOR_PANEL, OMNIPAUSE_TOGGLE)),
        DashboardRectItem(layout.help_button, fill=_press_fill(COLOR_PANEL, HELP_REFERENCE)),
        DashboardRectItem(layout.fmode_panel, fill=_press_fill(fmode_fill, FMODE_PANEL)),
        DashboardRectItem(layout.voice_panel, fill=_press_fill(voice_fill, VOICE_TOGGLE)),
    )
    _font_symbol = make_font(FONT_SYMBOL, 13, bold=True)
    _font_ui = make_font(FONT_UI, SIZE_SMALL, bold=True)
    # The app-name lockup, styled like the loading screen: pink, bold italic.
    # Built fresh (not via the cached make_font) so setItalic cannot leak into
    # every other user of a shared QFont.
    _font_app = QFont(FONT_UI, SIZE_BODY)
    _font_app.setBold(True)
    _font_app.setItalic(True)

    texts = (
        DashboardTextItem("\u23FB", layout.quit_button, font=_font_symbol),
        DashboardTextItem(omnipause_icon, layout.omnipause_button, font=_font_symbol),
        DashboardTextItem("?", layout.help_button, font=_font_ui),
        DashboardTextItem("Fun Time", layout.app_title, color=COLOR_APP_TITLE,
                          anchor="w", font=_font_app),
    )
    _chip_h = layout.fmode_panel.height
    images = (
        DashboardImageItem(_load_icon_pixmap("icon.ico", layout.app_icon.height), layout.app_icon),
        DashboardImageItem(_load_icon_pixmap("fmode_icon.ico", _chip_h), layout.fmode_panel),
        DashboardImageItem(
            _draw_mic_pixmap(layout.voice_panel.width, layout.voice_panel.height),
            layout.voice_panel,
        ),
    )
    actions = (
        (QUIT_BUTTON, layout.quit_button),
        (OMNIPAUSE_TOGGLE, layout.omnipause_button),
        (HELP_REFERENCE, layout.help_button),
        (FMODE_PANEL, layout.fmode_panel),
        (VOICE_TOGGLE, layout.voice_panel),
    )
    return DashboardScene(
        width=width,
        height=layout.height,
        rects=rects,
        texts=texts,
        images=images,
        actions=actions,
        hover_texts=tuple(
            (rect, _ACTION_TOOLTIPS[action_id]) for action_id, rect in actions
        ),
    )


# ---------------------------------------------------------------------------
# PyQt6 rendering widget
# ---------------------------------------------------------------------------
from PyQt6.QtCore import Qt, QEvent, QRectF, QPointF, pyqtSignal
from PyQt6.QtWidgets import QWidget, QToolTip, QDialog, QHBoxLayout, QVBoxLayout, QTextBrowser
from PyQt6.QtGui import QPainter, QPen, QBrush, QPixmap


class DashboardWidget(QWidget):
    """Custom widget that paints a DashboardScene using QPainter."""

    action_triggered = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: DashboardScene | None = None
        self.setMouseTracking(True)

    def set_scene(self, scene: DashboardScene) -> None:
        self._scene = scene
        # Sized to its contents: the bar shares its row with the log's filter
        # controls now, so it takes only the width its own buttons need and leaves
        # the rest of the row to them.
        self.setFixedSize(scene.width, scene.height)
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802
        scene = self._scene
        if scene is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QBrush(COLOR_BG))

        _default_font = make_font(FONT_UI, SIZE_SMALL, bold=True)

        for item in scene.rects:
            p.setPen(QPen(item.outline, 1))
            p.setBrush(QBrush(item.fill))
            p.drawRect(item.rect.x, item.rect.y, item.rect.width, item.rect.height)

        for item in scene.texts:
            if item.rect.width == 0 and item.rect.height == 0:
                continue
            p.setPen(QPen(item.color))
            p.setFont(item.font if item.font is not None else _default_font)
            rect = QRectF(item.rect.x, item.rect.y, item.rect.width, item.rect.height)
            flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            if item.anchor == "w":
                flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elif item.anchor == "n":
                flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            p.drawText(rect, flags, item.text)

        for item in scene.images:
            if item.pixmap.isNull():
                continue
            # Center the square pixmap within the (possibly wider) rect
            px_x = item.rect.x + (item.rect.width - item.pixmap.width()) // 2
            px_y = item.rect.y + (item.rect.height - item.pixmap.height()) // 2
            p.drawPixmap(px_x, px_y, item.pixmap)

        p.end()

    def mousePressEvent(self, event: object) -> None:  # noqa: N802
        scene = self._scene
        if scene is None:
            return
        pos = event.position()
        x, y = int(pos.x()), int(pos.y())
        for action_id, rect in scene.actions:
            if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
                self.action_triggered.emit(action_id)
                return

    def mouseMoveEvent(self, event: object) -> None:  # noqa: N802
        scene = self._scene
        if scene is None:
            return
        pos = event.position()
        x, y = int(pos.x()), int(pos.y())
        for rect, text in scene.hover_texts:
            if rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
                QToolTip.showText(self.mapToGlobal(event.position().toPoint()), text, self)
                return
        QToolTip.hideText()


class ReferenceDialog(QDialog):
    """Modeless popup listing every hotkey and voice command.

    Carries no in-window heading — its content title lives on the window chrome
    ("Hotkeys & Voice Commands Reference") — and is sized/placed by the caller to fill the
    Random Favs Browser's rect.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hotkeys & Voice Commands Reference")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(False)
        browser.setHtml(render_reference_html())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(browser)


def write_dashboard_command(path: Path, action_id: str) -> None:
    """Post a dashboard button (or voice-toggle) action for the dispatch loop.

    Robust to the dispatch loop's ~20 Hz rename-drain of the same file: the append
    retries past the transient sharing violation rather than raising into the Qt
    slot.  Unhandled, that error propagates out of a click slot and PyQt6 aborts
    the whole window — the "power button closed the Dash instead of quitting Fun
    Time" bug — so a persistently locked file drops the line and the next click
    lands.
    """
    append_command(path, action_id)


def apply_dashboard_window_geometry(
    window: QWidget,
    snapshot: DashboardSnapshot | None,
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
    if snapshot is None or snapshot.window.width <= 0 or snapshot.window.height <= 0:
        window.resize(scene.width, scene.height)
        return
    window.setGeometry(
        snapshot.window.x, snapshot.window.y,
        snapshot.window.width, snapshot.window.height,
    )


PRESS_FLASH_S = 0.2


from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QApplication


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
        start_minimized: bool = False,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        self._bar_layout = bar_layout
        self._launch_geometry = launch_geometry
        # The Random Favs Browser's screen rect; the reference popup opens over it.
        self._rfb_rect = rfb_rect
        # While the loading overlay is up the dashboard stays fully hidden so its
        # always-on-top window neither flashes above the overlay nor animates a
        # minimize on the way there (a hidden window renders nothing and the
        # geometry re-assert is gated on not-deferred).  We auto-detect that from
        # the loading screen's progress file and reveal ourselves once it is gone
        # — the launcher does not have to pass --start-minimized.  Neither a
        # loading-defer nor a persisted-minimized start may mirror its initial
        # off-screen state onto the other windows.
        self._deferred_for_loading = loading_screen_active(app_config.manifest_path.parent)
        self._suppress_minimize_routing = start_minimized or self._deferred_for_loading

        # Set on close, so the poller and press listener wind down with the
        # window instead of reading the player status files for the life of the
        # process.  Under test, several dashboards are built and closed in one
        # process, and leaked pollers would keep running past their window.
        self._stopping = threading.Event()
        self._pressed: dict[str, float] = {}
        self._reference_dialog: ReferenceDialog | None = None
        self._last_snapshot: DashboardSnapshot | None = None
        self._press_queue: queue.Queue[str] = queue.Queue()

        self.setWindowTitle("Fun Time")
        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        # The window spans the whole left column: the schematic on the left and
        # the log stream filling the strip beside it.  The log used to be a second
        # top-level window the bridge tracked by title; embedding it as a child
        # lets it ride the dashboard's topmost band, minimize/restore and close.
        self._widget = DashboardWidget()
        self._widget.action_triggered.connect(self._on_action)
        state_dir = app_config.manifest_path.parent
        self._log_widget = LogPanelWidget(event_log_path(state_dir), prefs_path(state_dir))
        # The top bar and the log's filter controls share one row — the bar's own
        # buttons on the left, the log controls filling the rest — so the Dash is a
        # row shorter than when the controls sat above the log, and the Random Favs
        # Browser below it that much taller.  The log stream fills the rest.
        top_row = QWidget(self)
        top_layout = QHBoxLayout(top_row)
        # The bar insets its own contents by PAD; the filters at the far end get
        # the same margin, so the row is even about its two edges.
        top_layout.setContentsMargins(0, 0, BAR_PAD, 0)
        top_layout.setSpacing(0)
        top_layout.addWidget(self._widget)
        # Right-justified: the log's filters and the bar's own buttons do
        # different jobs, and run together at the left they read as one strip.
        top_layout.addStretch(1)
        top_layout.addWidget(self._log_widget.controls)
        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(top_row)
        central_layout.addWidget(self._log_widget, 1)
        self.setCentralWidget(central)

        if launch_geometry is not None:
            self.setGeometry(
                launch_geometry.x, launch_geometry.y,
                launch_geometry.width, launch_geometry.height,
            )

        # Title-bar controls: keep minimize + close, drop maximize (the schematic
        # is a fixed size).  Close routes through closeEvent (quits everything);
        # minimize routes through changeEvent (omniminimize).
        # Show in taskbar via WS_EX_APPWINDOW.
        # The subprocess is launched with SW_HIDE (hidden_subprocess_kwargs),
        # which PyQt6 inherits.  winId() realizes the native window handle
        # without showing it, so during the loading overlay the window stays
        # fully hidden — no flash, no minimize animation, nothing on screen —
        # and _maybe_reveal_after_loading shows it once the overlay closes.
        _hwnd = int(self.winId())
        self._dash_hwnd = _hwnd
        SW_HIDE = 0
        SW_SHOW = 5
        SW_SHOWMINNOACTIVE = 7
        if self._deferred_for_loading:
            ctypes.windll.user32.ShowWindow(_hwnd, SW_HIDE)
        elif start_minimized:
            self.show()
            ctypes.windll.user32.ShowWindow(_hwnd, SW_SHOWMINNOACTIVE)
        else:
            self.show()
            ctypes.windll.user32.ShowWindow(_hwnd, SW_SHOW)
        WS_SYSMENU = 0x00080000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        _style = ctypes.windll.user32.GetWindowLongW(_hwnd, -16)  # GWL_STYLE
        _style = (_style | WS_SYSMENU | WS_MINIMIZEBOX) & ~WS_MAXIMIZEBOX
        ctypes.windll.user32.SetWindowLongW(_hwnd, -16, _style)
        _ex = ctypes.windll.user32.GetWindowLongW(_hwnd, -20)  # GWL_EXSTYLE
        ctypes.windll.user32.SetWindowLongW(_hwnd, -20, (_ex | 0x00040000) & ~0x00000080)
        ctypes.windll.user32.SetWindowPos(
            _hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020,
        )

        self._ahk_cmd_file = app_config.manifest_path.parent / "ahk_cmd.txt"

        # UDP press listener
        self._press_received.connect(self._handle_press_event)
        self._press_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._press_sock.bind(("127.0.0.1", 0))
        press_port = self._press_sock.getsockname()[1]
        port_file = app_config.dashboard_state_file.parent / "dashboard_press_port.txt"
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(press_port), encoding="utf-8")
        threading.Thread(target=self._press_listener, daemon=True, name="press-listener").start()

        # Notice overlays: flash each new event-log notice over the player it is
        # about.  A dedicated tail (its own offset) polls the shared file a touch
        # faster than the 500ms refresh so a "Clip saved" lands promptly.
        self._player_rects = self._compute_player_rects()
        self._notice_overlay = NoticeOverlay() if self._player_rects is not None else None
        self._notice_offset = 0
        self._notice_timer = QTimer(self)
        self._notice_timer.timeout.connect(self._poll_notices)
        self._notice_timer.start(250)

        # Refresh timer (500ms)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh)
        self._refresh_timer.start(500)
        self._refresh()

    def closeEvent(self, event: object) -> None:  # noqa: N802
        try:
            self._ahk_cmd_file.write_text("exit", encoding="utf-8")
        except OSError:
            pass
        self._stop_background_work()
        event.accept()

    def _stop_background_work(self) -> None:
        """Wind down the timers, threads, socket, and the log strip's tail.

        Closing the dashboard ends the session, so in production this only tidies
        up ahead of the process being killed.  It matters where a dashboard is
        built and closed inside a longer-lived process — the poller would
        otherwise keep reading the player status files forever.
        """
        self._stopping.set()
        self._refresh_timer.stop()
        self._notice_timer.stop()
        self._log_widget.shutdown()
        try:
            self._press_sock.close()  # unblocks the listener's recvfrom
        except OSError:
            pass
        if self._notice_overlay is not None:
            self._notice_overlay.shutdown()
            self._notice_overlay = None

    def changeEvent(self, event: object) -> None:  # noqa: N802
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
        if self._suppress_minimize_routing:
            return  # startup minimize (loading overlay) — not a user gesture
        if now_minimized and not was_minimized:
            write_dashboard_command(self._app_config.dashboard_cmd_file, OMNIMINIMIZE)

    def _maybe_route_omnirestore(self, *, now_minimized: bool, was_minimized: bool) -> None:
        """Write the omnirestore command on the minimized -> not-minimized edge only."""
        if was_minimized and not now_minimized and self._suppress_minimize_routing:
            # The post-loading reveal restored us; routing is live from here.
            self._suppress_minimize_routing = False
            return
        if was_minimized and not now_minimized:
            write_dashboard_command(self._app_config.dashboard_cmd_file, OMNIRESTORE)

    def _maybe_reveal_after_loading(self) -> None:
        """Show the window once the loading overlay is gone.

        The dashboard stays fully hidden (SW_HIDE, never Qt-shown) while the
        overlay is up, so it neither flashes above the overlay nor animates a
        minimize.  The overlay deletes its progress file when it closes, which
        is our cue to reveal.  Revealing from hidden does not fire a
        minimize->restore edge, so we clear the startup-minimize suppression
        here rather than relying on _maybe_route_omnirestore.
        """
        if not self._deferred_for_loading:
            return
        if loading_screen_active(self._app_config.manifest_path.parent):
            return
        self._deferred_for_loading = False
        self._suppress_minimize_routing = False
        self.show()
        SW_SHOW = 5
        ctypes.windll.user32.ShowWindow(self._dash_hwnd, SW_SHOW)
        ctypes.windll.user32.SetWindowPos(
            self._dash_hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020,
        )

    def _compute_pressed(self) -> frozenset[str]:
        now = time.monotonic()
        active = frozenset(aid for aid, t in self._pressed.items() if now - t < PRESS_FLASH_S)
        for aid in [a for a, t in self._pressed.items() if now - t >= PRESS_FLASH_S]:
            del self._pressed[aid]
        return active

    def _sync_own_topmost(self, omni_paused: bool) -> None:
        """Keep the dashboard's own topmost band in step with OmniPause.

        The dashboard floats over the players via WindowStaysOnTopHint, but
        OmniPause must free the desktop.  The orchestrator tries to drop it, yet
        its lookup for this Qt window (whose pid differs from the launcher's)
        intermittently fails, so the dashboard corrects its OWN band here using
        its reliable handle: non-topmost while paused, topmost otherwise.  It is
        drift correction — SetWindowPos runs only when the actual band differs
        from the desired one, so a Qt re-assert of the hint is undone on the next
        refresh with no flicker in the steady state.
        """
        desired_topmost = not omni_paused
        if is_window_topmost(self._dash_hwnd) != desired_topmost:
            set_always_on_top(self._dash_hwnd, desired_topmost)

    def _do_render(
        self,
        snapshot: DashboardSnapshot | None,
        pressed_actions: frozenset[str],
    ) -> None:
        self._last_snapshot = snapshot
        # OmniPause must free the desktop; drop our own topmost while paused
        # (the orchestrator's drop of this window is unreliable) and restore it
        # after.  See _sync_own_topmost.  The log strip is a child widget, so it
        # rides this window's band automatically.
        omni_paused = snapshot is not None and snapshot.omni_paused
        self._sync_own_topmost(omni_paused)
        state_dir = self._app_config.dashboard_state_file.parent
        scene = build_dashboard_scene(
            self._bar_layout,
            snapshot,
            width=self._bar_layout.content_width,
            pressed_actions=pressed_actions,
        )
        # While minimized, re-asserting geometry would restore the window and
        # fight the omniminimize — leave it minimized until the user restores it.
        # While deferred for loading it is hidden; don't touch it until reveal.
        if not self.isMinimized() and not self._deferred_for_loading:
            apply_dashboard_window_geometry(self, snapshot, scene, launch_geometry=self._launch_geometry)
        self._widget.set_scene(scene)

    def _on_action(self, action_id: str) -> None:
        if action_id == HELP_REFERENCE:
            self._toggle_reference_dialog()
            return
        self._pressed[action_id] = time.monotonic()
        write_dashboard_command(self._app_config.dashboard_cmd_file, action_id)
        self._do_render(self._last_snapshot, self._compute_pressed())
        QTimer.singleShot(
            int(PRESS_FLASH_S * 1000) + 10,
            lambda: self._do_render(self._last_snapshot, self._compute_pressed()),
        )

    def _toggle_reference_dialog(self) -> None:
        """Open the reference popup, or close it if it is already showing.

        Drives both the ``?`` button and the "help"/"reference"/… voice phrases:
        the same trigger opens and dismisses.
        """
        if self._reference_dialog is not None and self._reference_dialog.isVisible():
            self._reference_dialog.close()
        else:
            self._show_reference_dialog()

    def _show_reference_dialog(self) -> None:
        """Open (or re-focus) the hotkey/voice reference popup.

        On first open it is sized and placed to fill the Random Favs Browser's
        rect, so the reference occupies the exact same space; later opens keep
        wherever the user moved it.
        """
        if self._reference_dialog is None:
            self._reference_dialog = ReferenceDialog(self)
            if self._rfb_rect is not None:
                self._fit_reference_frame_to_rect(self._rfb_rect)
        self._reference_dialog.show()
        self._reference_dialog.raise_()
        self._reference_dialog.activateWindow()

    def _fit_reference_frame_to_rect(self, rect: Rect) -> None:
        """Size the reference popup so its whole frame — title bar included —
        fills *rect*, rather than its client area (which left the chrome
        overhanging the top).  Frame margins are known only once the window is
        realized, so place it at the rect, show it, measure, then inset the
        client to fill the frame."""
        dialog = self._reference_dialog
        assert dialog is not None
        dialog.setGeometry(rect.x, rect.y, rect.width, rect.height)
        dialog.show()
        frame = dialog.frameGeometry()
        client = dialog.geometry()
        x, y, w, h = client_rect_filling_frame(
            rect,
            left=client.left() - frame.left(),
            top=client.top() - frame.top(),
            right=frame.right() - client.right(),
            bottom=frame.bottom() - client.bottom(),
        )
        dialog.setGeometry(x, y, w, h)

    def _close_reference_dialog(self) -> None:
        """Dismiss the reference popup if it is open (the "close …" voice phrases)."""
        if self._reference_dialog is not None:
            self._reference_dialog.close()

    def _handle_press_event(self) -> None:
        toggle_reference = False
        close_reference = False
        while True:
            try:
                action = self._press_queue.get_nowait()
                if action == HELP_REFERENCE:
                    toggle_reference = True
                elif action == HELP_REFERENCE_CLOSE:
                    close_reference = True
                self._pressed[action] = time.monotonic()
            except queue.Empty:
                break
        # Voice arrives here as a press (the ? button drives _on_action directly):
        # "help"/… toggles the popup, "close help"/… only dismisses it.
        if close_reference:
            self._close_reference_dialog()
        if toggle_reference:
            self._toggle_reference_dialog()
        self._do_render(self._last_snapshot, self._compute_pressed())
        QTimer.singleShot(
            int(PRESS_FLASH_S * 1000) + 10,
            lambda: self._do_render(self._last_snapshot, self._compute_pressed()),
        )

    def _press_listener(self) -> None:
        while not self._stopping.is_set():
            try:
                data, _ = self._press_sock.recvfrom(256)
                self._press_queue.put(data.decode("utf-8").strip())
                self._press_received.emit()
            except OSError:
                break

    def _compute_player_rects(self) -> PlayerRects | None:
        """Where each notice-bearing window sits, in real screen coordinates.

        Derived from the same layout functions startup positions the windows
        with, so the overlay lands on the window rather than near it.  Returns
        None when the monitors can't be read (e.g. a headless run) so notices
        simply don't flash instead of crashing the dashboard.
        """
        try:
            monitors = enumerate_monitors()
            main_rect, secondary_rect = get_logical_monitor_rects(
                monitors,
                main_index=self._app_config.layout.main_monitor,
                secondary_index=self._app_config.layout.secondary_monitor,
            )
        except (ValueError, OSError):
            return None
        plan = compute_window_layout(
            main_monitor=main_rect,
            secondary_monitor=secondary_rect,
            layout_config=self._app_config.layout,
        )
        primary = compute_primary_media_rect(
            secondary_monitor=secondary_rect, layout_config=self._app_config.layout,
        )
        as_rect = lambda w: Rect(w.x, w.y, w.width, w.height)  # noqa: E731
        return PlayerRects(
            primary=as_rect(primary),
            portrait=as_rect(plan.portrait),
            landscape=as_rect(plan.landscape),
            dash=as_rect(plan.dashboard),
        )

    def _poll_notices(self) -> None:
        """Flash every new announcement over the player it concerns."""
        if self._notice_overlay is None or self._player_rects is None:
            return
        if self._deferred_for_loading:
            return
        records, self._notice_offset = read_events(
            self._app_config.dashboard_state_file.parent / EVENT_LOG_FILENAME,
            self._notice_offset,
        )
        for record in records:
            if is_announcement(record):
                target = notice_target_rect(record.source, self._player_rects)
                self._notice_overlay.flash(record, target)

    def _refresh(self) -> None:
        self._maybe_reveal_after_loading()
        self._do_render(
            load_dashboard_snapshot(self._app_config.dashboard_state_file),
            self._compute_pressed(),
        )


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
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    # The Random Favs Browser's rect — the reference popup opens over it.
    parser.add_argument("--rfb-x", type=int)
    parser.add_argument("--rfb-y", type=int)
    parser.add_argument("--rfb-width", type=int)
    parser.add_argument("--rfb-height", type=int)
    parser.add_argument(
        "--start-minimized",
        action="store_true",
        help="Start minimized (used while the loading screen covers startup)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Set AppUserModelID before any window creation so the taskbar can group
    # this process's windows with the pinned "Fun Time" shortcut.
    from .win32 import APP_USER_MODEL_ID, set_app_user_model_id
    try:
        set_app_user_model_id(APP_USER_MODEL_ID)
    except OSError:
        pass  # Non-fatal — taskbar grouping just won't work

    app = QApplication.instance() or QApplication([])

    app_config = load_dashboard_app_config(Path(args.manifest_path))
    launch_geometry = None
    if None not in {args.x, args.y, args.width, args.height}:
        launch_geometry = DashboardLaunchGeometry(
            x=args.x,
            y=args.y,
            width=args.width,
            height=args.height,
        )
    rfb_rect = None
    if None not in {args.rfb_x, args.rfb_y, args.rfb_width, args.rfb_height}:
        rfb_rect = Rect(args.rfb_x, args.rfb_y, args.rfb_width, args.rfb_height)
    _window = build_dashboard_window(
        app_config,
        launch_geometry=launch_geometry,
        rfb_rect=rfb_rect,
    )
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

