"""PyQt5-based remote desktop zoom tool.

This module implements a frameless, always-on-top window that continuously
captures a region around the mouse cursor from the primary monitor using
`mss` and displays a zoomed-in view. Global hotkeys are provided via the
`keyboard` library to toggle visibility and quit the application.
"""

import logging
import sys
import threading

import keyboard
import mss
from PyQt5.QtCore import QPoint, QTimer, Qt
from PyQt5.QtGui import QCursor, QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from core_capture import (
    capture_zoom_region,
    compute_window_size,
    get_primary_monitor,
)


# =========================
# Configuration
# =========================

# Size (in pixels) of the square region captured around the mouse cursor
ZOOM_SIZE: int = 300

# How much to scale the captured region when rendering in the window
ZOOM_FACTOR: float = 2.0

# Timer interval in milliseconds (lower = smoother but more CPU)
UPDATE_INTERVAL_MS: int = 16  # ~60 FPS


class ZoomWindow(QWidget):
    """Window that displays a zoomed-in view of the primary monitor.

    The window is frameless, always on top, and periodically captures an area
    around the mouse cursor from the primary monitor using a shared `mss`
    instance. The captured region is then scaled and rendered into the widget.

    Attributes:
        sct (mss.mss): Shared screen capture instance.
        primary_monitor (dict): Descriptor of the primary monitor from `mss`.
        label (QLabel): Label used to display the zoomed pixmap.
        timer (QTimer): Timer driving periodic screen captures.
    """

    def __init__(self, sct: mss.mss) -> None:
        super().__init__()

        self.sct = sct
        self._frame_bytes = None  # keep reference so QImage data stays valid

        # Use primary monitor only (index 1 in mss)
        self.primary_monitor = get_primary_monitor(self.sct)

        self.label = QLabel(alignment=Qt.AlignCenter)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.setLayout(layout)

        # Frameless, always on top, tool window (doesn't show in taskbar)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )

        # Fixed size based on zoom factor and capture size
        window_size = compute_window_size(ZOOM_SIZE, ZOOM_FACTOR)
        self.setFixedSize(window_size, window_size)

        # Semi-transparent close button in the top-right corner
        self._init_close_button()

        # Position window near top-right of primary monitor by default
        pm = self.primary_monitor
        x = pm["left"] + pm["width"] - window_size - 20
        y = pm["top"] + 20
        self.move(x, y)

        # Timer for continuous updates
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(UPDATE_INTERVAL_MS)

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

    def update_frame(self) -> None:
        """Capture and display the zoomed region around the current cursor.

        Captures a `ZOOM_SIZE` square around the cursor from the primary
        monitor only, converts it into a `QPixmap`, scales it by
        `ZOOM_FACTOR`, and sets it on the window label.
        """
        # Get current global cursor position
        cursor_pos: QPoint = QCursor.pos()
        cx, cy = cursor_pos.x(), cursor_pos.y()

        # Capture only from the primary monitor (avoids screen inception)
        sct_img = capture_zoom_region(
            self.sct,
            cx,
            cy,
            self.primary_monitor,
            ZOOM_SIZE,
        )

        # mss gives BGRA; use .rgb (RGB) for QImage
        self._frame_bytes = sct_img.rgb  # keep alive on self
        w, h = sct_img.width, sct_img.height

        qimg = QImage(self._frame_bytes, w, h, 3 * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        # Scale up by ZOOM_FACTOR
        target_w = int(ZOOM_SIZE * ZOOM_FACTOR)
        target_h = int(ZOOM_SIZE * ZOOM_FACTOR)
        pixmap = pixmap.scaled(
            target_w,
            target_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.label.setPixmap(pixmap)


def start_keyboard_hotkeys(app: QApplication, window: ZoomWindow) -> None:
    """Register global hotkeys for controlling the zoom window.

    Uses the `keyboard` library to bind global shortcuts:

    - `Esc`: Quit the Qt application.
    - `Ctrl+Space`: Toggle visibility of the zoom window.

    Args:
        app (QApplication): Running Qt application instance.
        window (ZoomWindow): Zoom window whose visibility is toggled.
    """

    def quit_app() -> None:
        app.quit()

    def toggle_window() -> None:
        if window.isVisible():
            window.hide()
        else:
            window.show()

    keyboard.add_hotkey("esc", quit_app)
    keyboard.add_hotkey("ctrl+space", toggle_window)


def main() -> None:
    """Entry point for the zoom tool application.

    Initializes the Qt application, creates a shared `mss` instance, and
    constructs the `ZoomWindow`. Keyboard hotkeys are started in a background
    thread so they do not block the Qt event loop.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logging.getLogger(__name__).info("Starting Surgical Zooming application.")

    app = QApplication(sys.argv)

    # Use a single MSS instance for efficiency
    with mss.mss() as sct:
        zoom_window = ZoomWindow(sct)
        zoom_window.show()

        # Run keyboard hooks in a background thread so they don't block the Qt loop
        threading.Thread(
            target=start_keyboard_hotkeys, args=(app, zoom_window), daemon=True
        ).start()

        sys.exit(app.exec_())


if __name__ == "__main__":
    import fire

    fire.Fire(main)

