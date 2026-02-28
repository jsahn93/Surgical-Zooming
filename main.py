"""PyQt5-based remote desktop zoom tool.

This module wires together the core capture engine and the PyQt5 zoom
window, and exposes a CLI-friendly `main()` entry point. Global hotkeys
are provided via the `keyboard` library to toggle visibility and quit
the application.
"""

import logging
import sys
import threading

import keyboard
from PyQt5.QtWidgets import QApplication

from core_capture import compute_window_size
from core.capture_engine import CaptureEngine
from gui.zoom_window import ZoomWindow


# =========================
# Configuration
# =========================

ZOOM_SIZE: int = 300
ZOOM_FACTOR: float = 2.0
UPDATE_INTERVAL_MS: int = 16  # ~60 FPS


def start_keyboard_hotkeys(app: QApplication, window: ZoomWindow) -> None:
    """Register global hotkeys for controlling the zoom window.

    Uses the `keyboard` library to bind global shortcuts:

    - `Esc`: Quit the Qt application.
    - `Ctrl+Space`: Toggle visibility of the zoom window.

    Args:
        app: Running Qt application instance.
        window: Zoom window whose visibility is toggled.
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

    Initializes the Qt application, creates a shared `CaptureEngine`
    instance, and constructs the `ZoomWindow`. Keyboard hotkeys are
    started in a background thread so they do not block the Qt event loop.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logging.getLogger(__name__).info("Starting Surgical Zooming application.")

    app = QApplication(sys.argv)

    with CaptureEngine() as engine:
        zoom_window = ZoomWindow(
            engine=engine,
            zoom_size=ZOOM_SIZE,
            zoom_factor=ZOOM_FACTOR,
            update_interval_ms=UPDATE_INTERVAL_MS,
        )

        threading.Thread(
            target=start_keyboard_hotkeys,
            args=(app, zoom_window),
            daemon=True,
        ).start()

        sys.exit(app.exec_())


if __name__ == "__main__":
    import fire

    fire.Fire(main)
