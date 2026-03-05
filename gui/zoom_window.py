"""Zoom window GUI for Surgical Zooming.

This module contains the PyQt5-based zoom window implementation, separated
from the capture engine and other core logic.

The window is intentionally a "dumb" view: it asks the core capture engine
for frames around the current cursor position and displays them, without
performing any capture math itself.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from PyQt5.QtCore import QPoint, QPointF, QTimer, Qt
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QScreen,
)
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.capture_engine import CaptureEngine
from core_capture import compute_capture_dimensions


class CursorOverlay(QWidget):
    """Synthetic cursor overlay: vector-rendered arrow at canvas center.

    mss captures bypass the hardware cursor; QCursor.pixmap() is unreliable
    on Windows (returns null). This overlay draws a high-contrast vector
    arrow at the center so it matches the real cursor position without
    depending on OS cursor extraction (single-binary PyInstaller safe).
    """

    SIZE = 32

    # Classic asymmetric arrow pointer (tip at origin, body toward +x,+y).
    _ARROW = QPolygonF([
        QPointF(0, 0),    # tip (hotspot)
        QPointF(0, 20),   # bottom left
        QPointF(6, 14),   # notch inner
        QPointF(10, 18),  # notch outer
        QPointF(18, 2),   # top right
    ])

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.SIZE, self.SIZE)

    def paintEvent(self, event: object) -> None:
        """Draw a vector arrow (white fill, black outline) centered in the overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Center of overlay = center of window (hotspot)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        painter.translate(cx, cy)

        painter.setBrush(QBrush(QColor(255, 255, 255), Qt.SolidPattern))
        painter.setPen(QPen(QColor(0, 0, 0), 2, Qt.SolidLine))
        painter.drawPolygon(self._ARROW)


HUD_STYLE = """
    QFrame {
        background-color: rgba(20, 20, 20, 220);
        border-radius: 8px;
        color: #E0E0E0;
        font-family: monospace;
    }
    QSlider::groove:horizontal {
        height: 6px;
        background: rgba(60, 60, 60, 200);
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        width: 14px;
        margin: -4px 0;
        background: #808080;
        border-radius: 7px;
    }
    QComboBox, QCheckBox {
        color: #E0E0E0;
        font-family: monospace;
    }
"""


class ProximityHUD(QFrame):
    """Dark frosted-glass overlay shown when cursor is on secondary display (ghost UI).

    Industrial, transient HUD with zoom slider, keybind selector, and S-Pen bypass.
    """

    ZOOM_MIN = 1.5
    ZOOM_MAX = 5.0

    def __init__(
        self,
        parent: QWidget,
        initial_zoom: float,
        on_zoom_changed: Any,
        initial_precision: bool = True,
        initial_toggle_bind: str = "caps_lock",
        on_toggle_rebind: Any | None = None,
        on_reset_defaults: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(HUD_STYLE)
        self.setObjectName("ProximityHUD")
        self._current_bind = initial_toggle_bind
        self._on_toggle_rebind = on_toggle_rebind
        self._on_reset_defaults = on_reset_defaults

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Zoom multiplier: slider 1.5x–5.0x + label
        zoom_label = QLabel("Zoom")
        zoom_label.setStyleSheet("color: #E0E0E0; font-family: monospace;")
        layout.addWidget(zoom_label)

        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setMinimum(int(self.ZOOM_MIN * 10))
        self._zoom_slider.setMaximum(int(self.ZOOM_MAX * 10))
        self._zoom_slider.setValue(int(initial_zoom * 10))
        self._zoom_slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._zoom_slider)

        self._zoom_value_label = QLabel(f"{initial_zoom:.1f}x")
        self._zoom_value_label.setStyleSheet("color: #E0E0E0; font-family: monospace;")
        layout.addWidget(self._zoom_value_label)

        self._on_zoom_changed = on_zoom_changed

        # Precision Mode (slow cursor when active)
        self._precision_check = QCheckBox("Precision Mode (Slow Mouse)")
        self._precision_check.setStyleSheet("color: #E0E0E0; font-family: monospace;")
        layout.addWidget(self._precision_check)

        # Toggle keybind
        key_label = QLabel("Toggle Bind")
        key_label.setStyleSheet("color: #E0E0E0; font-family: monospace;")
        layout.addWidget(key_label)
        self._toggle_button = QPushButton(
            self._format_bind_label(initial_toggle_bind), self
        )
        self._toggle_button.setStyleSheet(
            """
            QPushButton {
                color: #FFFFFF;
                font-family: monospace;
                padding: 4px 8px;
                background-color: rgba(60, 60, 60, 220);
                border-radius: 4px;
                border: 1px solid rgba(200, 200, 200, 80);
            }
            QPushButton:hover {
                background-color: rgba(90, 90, 90, 230);
            }
            QPushButton:pressed {
                background-color: rgba(40, 40, 40, 230);
            }
            """
        )
        self._toggle_button.clicked.connect(self._on_toggle_button_clicked)
        layout.addWidget(self._toggle_button)

        # S-Pen Mode (future-proofing): bypass HUD so hardware clicks hit canvas
        self._spen_check = QCheckBox("S-Pen Mode (Bypass HUD)")
        self._spen_check.setStyleSheet("color: #E0E0E0; font-family: monospace;")
        layout.addWidget(self._spen_check)

        # Reset to Default button – universal escape hatch
        self._reset_button = QPushButton("Reset to Default", self)
        self._reset_button.setStyleSheet(
            """
            QPushButton {
                color: #FFFFFF;
                font-family: monospace;
                padding: 4px 8px;
                background-color: rgba(80, 30, 30, 220);
                border-radius: 4px;
                border: 1px solid rgba(255, 120, 120, 120);
            }
            QPushButton:hover {
                background-color: rgba(140, 40, 40, 240);
            }
            QPushButton:pressed {
                background-color: rgba(60, 20, 20, 230);
            }
            """
        )
        self._reset_button.clicked.connect(self._on_reset_clicked)
        layout.addWidget(self._reset_button)

        # Force widgets to top/center and prevent slider from being clipped
        layout.addStretch()

        # Apply initial states from settings
        self._precision_check.setChecked(bool(initial_precision))

        self.setMinimumSize(400, 300)
        self.setFixedWidth(260)
        self.adjustSize()

    def _format_bind_label(self, bind: str) -> str:
        return f"Toggle Bind: {bind}"

    def _on_toggle_button_clicked(self) -> None:
        """Enter listening mode and delegate rebinding to the bridge callback."""
        self._toggle_button.setText("Listening... Press any key/click")
        if callable(self._on_toggle_rebind):
            self._on_toggle_rebind()

    def _on_slider_changed(self, value: int) -> None:
        factor = value / 10.0
        self._zoom_value_label.setText(f"{factor:.1f}x")
        if callable(self._on_zoom_changed):
            self._on_zoom_changed(factor)

    def set_zoom_value(self, value: float) -> None:
        """Sync slider from bridge (e.g. Ctrl+Scroll) without emitting."""
        self._zoom_slider.blockSignals(True)
        self._zoom_slider.setValue(int(max(self.ZOOM_MIN, min(self.ZOOM_MAX, value)) * 10))
        self._zoom_slider.blockSignals(False)
        self._zoom_value_label.setText(f"{value:.1f}x")

    def is_spen_bypass(self) -> bool:
        return self._spen_check.isChecked()

    def on_spen_toggled(self, callback: Any) -> None:
        self._spen_check.toggled.connect(callback)

    def on_precision_toggled(self, callback: Any) -> None:
        """Connect Precision Mode checkbox to callback(checked: bool)."""
        self._precision_check.toggled.connect(callback)

    def set_toggle_bind(self, bind: str) -> None:
        """Update the toggle bind button label from bridge changes."""
        self._current_bind = bind
        self._toggle_button.setText(self._format_bind_label(bind))

    def _on_reset_clicked(self) -> None:
        """Reset bridge and HUD controls back to safe defaults."""
        if callable(self._on_reset_defaults):
            self._on_reset_defaults()

        # UI sync: immediately reflect default state
        default_zoom = 2.0
        self.set_zoom_value(default_zoom)
        self._precision_check.setChecked(True)
        self.set_toggle_bind("caps_lock")


class ZoomWindow(QWidget):
    """Window that displays a zoomed-in view of the primary monitor.

    The window is frameless, always on top, and periodically asks the
    `CaptureEngine` for a cursor-centered frame from the primary monitor.
    It then scales and renders that frame into a label.

    The widget is designed to be placed on a specific virtual display at
    full-screen size.
    """

    def __init__(
        self,
        engine: CaptureEngine,
        zoom_size: int,
        zoom_factor: float,
        update_interval_ms: int,
        target_resolution: Tuple[int, int],
        target_screen: Optional[QScreen] = None,
        bridge: Optional[Any] = None,
    ) -> None:
        """Initialize the zoom window.

        Args:
            engine: Core capture engine responsible for providing frames.
            zoom_size: Base capture size (smaller side).
            zoom_factor: Zoom multiplier when rendering in the window.
            update_interval_ms: Requested timer interval; locked to 60 FPS (16 ms) to yield to event loop.
            target_resolution: Optional (width, height) for target display.
            bridge: Optional StateBridge for HUD; when provided, Proximity HUD is created.
        """
        super().__init__()

        self.engine = engine
        self.zoom_size = zoom_size
        self._zoom_factor = zoom_factor
        self._bridge = bridge
        # Lock capture interval to 60 FPS to prevent CPU starvation and yield to event loop
        self._capture_interval_ms = min(update_interval_ms, 1000 // 60)
        self.target_resolution = target_resolution
        self._target_screen = target_screen

        self._zoom_factor_min = 1.5
        self._zoom_factor_max = 5.0
        self._update_capture_dimensions()

        self._frame_bytes: Optional[bytes] = None
        self._spen_bypass_hud = False

        self.primary_monitor = self.engine.primary_monitor

        self._init_ui()
        self._init_timer()
        if bridge is not None:
            self._init_hud()

    def _update_capture_dimensions(self) -> None:
        """Recompute capture dimensions from zoom_size, zoom_factor, and target display.

        Uses the target screen's physical pixel size (logical size × devicePixelRatio)
        so anti-pillarboxing aspect ratio is applied after DPI correction, giving
        1:1 pixel fidelity. Capture dimensions are in physical pixels for mss.
        """
        effective_size = max(20, int(self.zoom_size / self._zoom_factor))
        if self._target_screen is not None:
            geom = self._target_screen.geometry()
            dpr = self._target_screen.devicePixelRatio()
            target_phys_w = max(1, int(geom.width() * dpr))
            target_phys_h = max(1, int(geom.height() * dpr))
            self._capture_width, self._capture_height = compute_capture_dimensions(
                effective_size, target_phys_w, target_phys_h
            )
        else:
            self._capture_width, self._capture_height = compute_capture_dimensions(
                effective_size,
                self.target_resolution[0],
                self.target_resolution[1],
            )

    def set_zoom_factor(self, value: float) -> None:
        """Set zoom factor from bridge (main thread); updates capture dimensions."""
        self._zoom_factor = max(
            self._zoom_factor_min,
            min(self._zoom_factor_max, value),
        )
        self._update_capture_dimensions()
        if getattr(self, "_hud", None) is not None:
            self._hud.set_zoom_value(self._zoom_factor)

    def adjust_zoom(self, delta: int) -> None:
        """Adjust zoom from accumulated delta (used internally after throttle)."""
        if not delta:
            return
        step = (delta / 120) * 0.1
        self._zoom_factor = max(
            self._zoom_factor_min,
            min(self._zoom_factor_max, self._zoom_factor + step),
        )
        self._update_capture_dimensions()

    def _init_ui(self) -> None:
        """Initialize window widgets, style, and placement."""
        self.label = QLabel(alignment=Qt.AlignCenter)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.setLayout(layout)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )

        # Synthetic cursor overlay: static crosshair at center (mss captures bypass hardware cursor)
        self._cursor_overlay = CursorOverlay(self)
        self._cursor_overlay.raise_()

        self._init_close_button()
        self._place_on_target_screen_fullscreen()

    def _init_hud(self) -> None:
        """Create Proximity HUD overlay and connect to bridge. HUD starts hidden."""
        def _reset_defaults() -> None:
            if self._bridge is None:
                return
            # Foundational defaults
            self._bridge.set_toggle_bind("caps_lock")
            self._bridge.set_zoom_factor(2.0)
            self._bridge.set_precision_mode(True)

        self._hud = ProximityHUD(
            self,
            initial_zoom=self._zoom_factor,
            on_zoom_changed=self._bridge.set_zoom_factor,
            initial_precision=self._bridge.is_precision_mode,
            initial_toggle_bind=getattr(self._bridge, "toggle_bind", "caps_lock"),
            on_toggle_rebind=self._bridge.start_rebinding,
            on_reset_defaults=_reset_defaults,
        )
        self._hud.on_spen_toggled(self._on_spen_bypass_toggled)
        self._hud.on_precision_toggled(
            lambda checked: self._bridge.set_precision_mode(checked)
        )
        self._bridge.toggle_bind_changed.connect(self._hud.set_toggle_bind)
        self._bridge.hud_visibility_changed.connect(self._on_hud_visibility_changed)
        self._hud.hide()
        self._hud.raise_()
        self._center_hud()

    def _on_spen_bypass_toggled(self, checked: bool) -> None:
        """S-Pen Mode: hide HUD and refuse to show until unchecked."""
        self._spen_bypass_hud = checked
        if checked:
            self._hud.hide()

    def _on_hud_visibility_changed(self, show: bool) -> None:
        """Slot for bridge.hud_visibility_changed: show/hide HUD (respect S-Pen bypass)."""
        if self._spen_bypass_hud:
            self._hud.hide()
            return
        if show:
            self._hud.show()
            self._hud.raise_()
            self._center_hud()
        else:
            self._hud.hide()

    def _center_hud(self) -> None:
        """Center the HUD overlay in the window."""
        if getattr(self, "_hud", None) is None:
            return
        rect = self.rect()
        hx = rect.x() + (rect.width() - self._hud.width()) // 2
        hy = rect.y() + (rect.height() - self._hud.height()) // 2
        self._hud.move(hx, hy)

    def _init_close_button(self) -> None:
        """Create a semi-transparent close button in the top-right corner."""
        button_size: int = 26
        margin: int = 4

        self.close_button = QPushButton("✕", self)
        self.close_button.setToolTip("Close viewer")
        self.close_button.setFixedSize(button_size, button_size)
        self.close_button.move(self.width() - button_size - margin, margin)

        self.close_button.setStyleSheet(
            """
            QPushButton {
                border: none;
                border-radius: 4px;
                background-color: rgba(0, 0, 0, 110);
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(220, 20, 60, 170);
            }
            """
        )
        self.close_button.clicked.connect(QApplication.instance().quit)

    def _place_on_target_screen_fullscreen(self) -> None:
        """Place the window on the selected display at full-screen.

        When a ``QScreen`` was provided at construction time, that display is
        used directly. Otherwise, the window defaults to the last screen in
        ``QApplication.screens()`` (common case for virtual displays / VDD).
        The window geometry matches the target display's native resolution
        for 1:1 pixel mapping.
        """
        app = QApplication.instance()
        if app is None:
            return

        target_screen: Optional[QScreen] = self._target_screen
        if target_screen is None:
            screens = app.screens()
            if screens:
                target_screen = screens[-1]

        if target_screen is not None:
            geometry = target_screen.geometry()
            self.setGeometry(geometry)

        # Shown via showFullScreen() when IS_ACTIVE (Glass Desktop)
        self._center_cursor_overlay()

    def resizeEvent(self, event: object) -> None:
        """Keep synthetic cursor overlay and HUD centered when window resizes."""
        super().resizeEvent(event)
        self._center_cursor_overlay()
        self._center_hud()

    def _center_cursor_overlay(self) -> None:
        """Position the synthetic cursor overlay at the center of the canvas."""
        cx = (self.width() - self._cursor_overlay.width()) // 2
        cy = (self.height() - self._cursor_overlay.height()) // 2
        self._cursor_overlay.move(cx, cy)

    def _init_timer(self) -> None:
        """Set up the periodic frame update timer. Interval locked to 60 FPS (16 ms) to yield to event loop."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(self._capture_interval_ms)

    def update_frame(self) -> None:
        """Capture and display the zoomed region. Skips when hidden or when HUD is visible (static clamped frame)."""
        if not self.isVisible():
            return
        if getattr(self, "_hud", None) is not None and self._hud.isVisible():
            return
        cursor_pos: QPoint = QCursor.pos()
        cursor_x, cursor_y = cursor_pos.x(), cursor_pos.y()

        # Primary screen DPR: Qt cursor is logical; core converts to physical for mss
        app = QApplication.instance()
        primary = app.primaryScreen() if app else None
        dpr = float(primary.devicePixelRatio()) if primary else 1.0

        screenshot = self.engine.capture_cursor_region(
            cursor_x, cursor_y, self._capture_width, self._capture_height, dpr
        )

        self._frame_bytes = screenshot.rgb
        width, height = screenshot.width, screenshot.height

        qimg = QImage(
            self._frame_bytes, width, height, 3 * width, QImage.Format_RGB888
        )
        pixmap = QPixmap.fromImage(qimg)

        # Scale to fill the target display; aspect ratio already matches (no pillarboxing)
        target_width = self.target_resolution[0]
        target_height = self.target_resolution[1]
        pixmap = pixmap.scaled(
            target_width,
            target_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.label.setPixmap(pixmap)
