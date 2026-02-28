"""Zoom window GUI for Surgical Zooming.

This module contains the PyQt5-based zoom window implementation, separated
from the capture engine and other core logic.

The window is intentionally a "dumb" view: it asks the core capture engine
for frames around the current cursor position and displays them, without
performing any capture math itself.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PyQt5.QtCore import QPoint, QTimer, Qt
from PyQt5.QtGui import QCursor, QImage, QPixmap, QScreen
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from core.capture_engine import CaptureEngine


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
        target_resolution: Optional[Tuple[int, int]] = (1366, 768),
    ) -> None:
        """Initialize the zoom window.

        Args:
            engine: Core capture engine responsible for providing frames.
            zoom_size: Side length, in pixels, of the square capture region.
            zoom_factor: Zoom multiplier when rendering in the window.
            update_interval_ms: Timer interval in milliseconds.
            target_resolution: Optional target resolution (width, height)
                used to choose the virtual display on which to show the
                window full-screen. If no screen matches, primary Qt screen
                is used as fallback.
        """
        super().__init__()

        self.engine = engine
        self.zoom_size = zoom_size
        self.zoom_factor = zoom_factor
        self.update_interval_ms = update_interval_ms
        self.target_resolution = target_resolution

        self._frame_bytes: Optional[bytes] = None

        self.primary_monitor = self.engine.primary_monitor

        self._init_ui()
        self._init_timer()

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

        self._init_close_button()
        self._place_on_target_screen_fullscreen()

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
        """Place the window on the target virtual display and go full-screen.

        The target display is chosen by matching the configured resolution
        (e.g. 1366x768). If no screen matches, the primary screen is used.
        """
        app = QApplication.instance()
        if app is None:
            return

        screens = app.screens()
        target_screen: Optional[QScreen] = None

        if self.target_resolution is not None:
            target_width, target_height = self.target_resolution
            for screen in screens:
                geom = screen.geometry()
                if geom.width() == target_width and geom.height() == target_height:
                    target_screen = screen
                    break

        if target_screen is None and screens:
            target_screen = screens[0]

        if target_screen is not None:
            geometry = target_screen.geometry()
            self.setGeometry(geometry)

        self.showFullScreen()

    def _init_timer(self) -> None:
        """Set up the periodic frame update timer."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(self.update_interval_ms)

    def update_frame(self) -> None:
        """Capture and display the zoomed region around the current cursor.

        Delegates all capture logic to the core capture engine; only handles
        conversion to QPixmap for display.
        """
        cursor_pos: QPoint = QCursor.pos()
        cursor_x, cursor_y = cursor_pos.x(), cursor_pos.y()

        screenshot = self.engine.capture_cursor_region(
            cursor_x, cursor_y, self.zoom_size
        )

        self._frame_bytes = screenshot.rgb
        width, height = screenshot.width, screenshot.height

        qimg = QImage(
            self._frame_bytes, width, height, 3 * width, QImage.Format_RGB888
        )
        pixmap = QPixmap.fromImage(qimg)

        target_width = int(self.zoom_size * self.zoom_factor)
        target_height = int(self.zoom_size * self.zoom_factor)
        pixmap = pixmap.scaled(
            target_width,
            target_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.label.setPixmap(pixmap)
