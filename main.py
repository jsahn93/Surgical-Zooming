"""PyQt5-based remote desktop zoom tool.

This module wires together the core capture engine and the PyQt5 zoom
window, and exposes a CLI-friendly `main()` entry point. Global hotkeys
and input hooks (Capslock dual-mode, Ctrl+Scroll zoom) run on background
threads and communicate with the GUI only via PyQt signals to avoid
thread contention and frame drops.
"""

import logging
import os
import sys
import threading
import time
from ctypes import byref, c_int, windll
from ctypes import wintypes

import keyboard
from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QScreen
from PyQt5.QtWidgets import QApplication

from core.capture_engine import CaptureEngine
from gui.zoom_window import ZoomWindow
from input_hooks import start_ctrl_scroll_hook

# Windows mouse speed API (Precision Mode)
SPI_SETMOUSESPEED = 0x0071
MOUSE_SPEED_STANDARD = 10
MOUSE_SPEED_PRECISION = 4

# =========================
# Configuration
# =========================

ZOOM_SIZE: int = 300
ZOOM_FACTOR: float = 2.0
CAPSLOCK_HOLD_THRESHOLD_MS: float = 300
# Fallback if no secondary display is found (removed hardcoding; used only as last resort)
DEFAULT_TARGET_RESOLUTION: tuple[int, int] = (1366, 768)

# Capture loop locked to 60 FPS to yield to event loop and prevent CPU starvation
CAPTURE_INTERVAL_MS: int = 1000 // 60  # 16 ms

# Scroll throttle: apply accumulated zoom at most every 16 ms (60 Hz)
SCROLL_THROTTLE_MS: int = 16

# Proximity HUD: poll interval for cursor vs primary monitor boundary (ms)
PROXIMITY_POLL_MS: int = 50


def resolve_resource_path(relative_path: str) -> str:
    """Return an absolute, PyInstaller-safe path for a bundled resource.

    This helper resolves `relative_path` under the PyInstaller extraction
    directory when running from a frozen binary, or under the project root
    when running from source. Callers should pass paths relative to the
    repository root (for example: ``assets/cursor.png`` or
    ``config/settings.json``).

    Args:
        relative_path: Resource location relative to the project root.

    Returns:
        Absolute filesystem path that works both in a PyInstaller bundle
        and in a standard Python environment.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.abspath(os.path.join(base_path, relative_path))


def _set_mouse_speed(speed: int) -> None:
    """Set Windows mouse speed (1–20). No-op on non-Windows. 10 = standard, 4 = slow (precision)."""
    if sys.platform != "win32":
        return
    try:
        # pvParam must be pointer to UINT; SPIF_UPDATEINIFILE = 0x01, SPIF_SENDCHANGE = 0x02
        windll.user32.SystemParametersInfoW(
            0x0071,  # SPI_SETMOUSESPEED
            0,
            byref(c_int(speed)),
            0x01 | 0x02,
        )
    except Exception:
        pass


def _get_cursor_x() -> int | None:
    """Return global cursor X (Windows). Returns None on non-Windows or failure."""
    if sys.platform != "win32":
        return None
    pt = wintypes.POINT()
    if windll.user32.GetCursorPos(byref(pt)):
        return pt.x
    return None


def _run_proximity_poll(get_primary_right, hud_signal):
    """Background loop: emit True when cursor X > primary right edge. Signal only; no GUI."""
    last = None
    while True:
        time.sleep(PROXIMITY_POLL_MS / 1000.0)
        x = _get_cursor_x()
        if x is None:
            continue
        try:
            primary_right = get_primary_right()
            show = x > primary_right
            if last is None or last != show:
                last = show
                hud_signal.emit(show)
        except Exception:
            pass


class StateBridge(QObject):
    """Thread-safe bridge: input hooks only emit signals; GUI updates run on main thread.

    Keyboard and scroll hooks must not call .hide()/.showFullScreen() or update
    mss/zoom state directly. They emit active_changed(bool) and zoom_delta_requested(int).
    Slots connected on the main thread perform the actual UI and state updates.
    """

    active_changed = pyqtSignal(bool)
    zoom_delta_requested = pyqtSignal(int)
    zoom_factor_changed = pyqtSignal(float)
    hud_visibility_changed = pyqtSignal(bool)
    precision_mode_changed = pyqtSignal(bool)
    quit_requested = pyqtSignal()

    def __init__(
        self,
        initial_zoom_factor: float,
        zoom_min: float = 0.5,
        zoom_max: float = 4.0,
    ) -> None:
        super().__init__()
        self._is_active = False
        self._zoom_factor = initial_zoom_factor
        self._zoom_min = zoom_min
        self._zoom_max = zoom_max
        self._accumulated_delta = 0
        self._throttle_timer_running = False
        self._throttle_timer = QTimer(self)
        self._throttle_timer.setSingleShot(True)
        self._throttle_timer.timeout.connect(self._flush_zoom_delta)
        self._lock = threading.Lock()
        self._precision_mode = False

    @property
    def is_precision_mode(self) -> bool:
        with self._lock:
            return self._precision_mode

    def set_precision_mode(self, value: bool) -> None:
        with self._lock:
            self._precision_mode = value
        self.precision_mode_changed.emit(self._precision_mode)

    def set_zoom_factor(self, value: float) -> None:
        """Set zoom factor from GUI (e.g. HUD slider); clamps and emits on main thread."""
        with self._lock:
            self._zoom_factor = max(
                self._zoom_min,
                min(self._zoom_max, value),
            )
        self.zoom_factor_changed.emit(self._zoom_factor)

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    def _set_active(self, value: bool) -> None:
        with self._lock:
            self._is_active = value

    def _flush_zoom_delta(self) -> None:
        """Run on main thread: apply accumulated scroll delta and emit new factor (throttled)."""
        if self._accumulated_delta == 0:
            self._throttle_timer_running = False
            return
        step = (self._accumulated_delta / 120) * 0.1
        self._zoom_factor = max(
            self._zoom_min,
            min(self._zoom_max, self._zoom_factor + step),
        )
        self._accumulated_delta = 0
        self._throttle_timer_running = False
        self.zoom_factor_changed.emit(self._zoom_factor)

    def on_zoom_delta(self, delta: int) -> None:
        """Called from main thread when scroll hook emits; accumulates and throttles at 60 Hz."""
        if delta == 0:
            return
        self._accumulated_delta += delta
        if not self._throttle_timer_running:
            self._throttle_timer_running = True
            self._throttle_timer.start(SCROLL_THROTTLE_MS)


def start_input_hooks(
    bridge: StateBridge,
    engine: CaptureEngine,
) -> None:
    """Register Esc, Capslock dual-mode, and Ctrl+Scroll. Only emit signals; no GUI calls."""

    def quit_app() -> None:
        bridge.quit_requested.emit()

    press_time: list[float] = [0.0]
    state_before: list[bool] = [False]

    def on_capslock_press(_: object) -> None:
        press_time[0] = time.monotonic()
        state_before[0] = bridge.is_active
        bridge._set_active(True)
        bridge.active_changed.emit(True)

    def on_capslock_release(_: object) -> None:
        elapsed_ms = (time.monotonic() - press_time[0]) * 1000
        if elapsed_ms >= CAPSLOCK_HOLD_THRESHOLD_MS:
            bridge._set_active(False)
            bridge.active_changed.emit(False)
        else:
            new_state = not state_before[0]
            bridge._set_active(new_state)
            bridge.active_changed.emit(new_state)

    keyboard.add_hotkey("esc", quit_app)
    keyboard.on_press_key("caps lock", on_capslock_press)
    keyboard.on_release_key("caps lock", on_capslock_release)

    def get_primary_bounds() -> tuple[int, int, int, int]:
        pm = engine.primary_monitor
        return pm["left"], pm["top"], pm["width"], pm["height"]

    def on_zoom_delta(delta: int) -> None:
        # Emit to main thread; bridge slot will throttle
        bridge.zoom_delta_requested.emit(delta)

    start_ctrl_scroll_hook(
        get_is_active=lambda: bridge.is_active,
        get_primary_bounds=get_primary_bounds,
        on_zoom_delta=on_zoom_delta,
    )


def main(monitor_index: int | None = None) -> None:
    """Entry point. Window starts hidden (Glass Desktop); activate via Capslock.

    Args:
        monitor_index: Optional zero-based index into the list returned by
            ``QGuiApplication.screens()``. When provided, the zoom window
            will occupy that display. When omitted or out of range, the last
            available screen is used (typical for virtual displays / VDD).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting Surgical Zooming application.")

    # Enable high-DPI awareness so Qt scales crisply on modern displays.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)

    screens = app.screens()
    if not screens:
        target_screen: QScreen | None = None
        target_resolution: tuple[int, int] = DEFAULT_TARGET_RESOLUTION
        logger.warning(
            "No Qt screens detected; falling back to default resolution %dx%d",
            *target_resolution,
        )
    else:
        screen_count = len(screens)
        # Default to last screen (most likely VDD/virtual display).
        if monitor_index is None:
            selected_index = screen_count - 1
        else:
            if monitor_index < 0 or monitor_index >= screen_count:
                logger.warning(
                    "Requested monitor_index %d out of range [0, %d); "
                    "defaulting to last screen.",
                    monitor_index,
                    screen_count,
                )
                selected_index = screen_count - 1
            else:
                selected_index = monitor_index

        target_screen = screens[selected_index]
        geom = target_screen.geometry()
        target_resolution = (geom.width(), geom.height())

        logger.info(
            "Detected %d screens; selected index %d: %s (%dx%d at %d,%d)",
            screen_count,
            selected_index,
            target_screen.name(),
            geom.width(),
            geom.height(),
            geom.x(),
            geom.y(),
        )

    bridge = StateBridge(
        initial_zoom_factor=ZOOM_FACTOR,
        zoom_min=1.5,
        zoom_max=5.0,
    )

    def update_mouse_speed() -> None:
        """Set system mouse speed: 4 (slow) when Precision Mode + Active, else 10 (standard)."""
        use_slow = bridge.is_precision_mode and bridge.is_active
        _set_mouse_speed(MOUSE_SPEED_PRECISION if use_slow else MOUSE_SPEED_STANDARD)

    try:
        with CaptureEngine() as engine:
            zoom_window = ZoomWindow(
                engine=engine,
                zoom_size=ZOOM_SIZE,
                zoom_factor=ZOOM_FACTOR,
                update_interval_ms=CAPTURE_INTERVAL_MS,
                target_resolution=target_resolution,
                target_screen=target_screen,
                bridge=bridge,
            )

            # All GUI updates on main thread via slots
            def on_active_changed(active: bool) -> None:
                if active:
                    zoom_window.showFullScreen()
                else:
                    zoom_window.hide()
                update_mouse_speed()

            bridge.active_changed.connect(on_active_changed)
            bridge.zoom_factor_changed.connect(zoom_window.set_zoom_factor)
            bridge.quit_requested.connect(app.quit)
            bridge.zoom_delta_requested.connect(bridge.on_zoom_delta)
            bridge.precision_mode_changed.connect(update_mouse_speed)

            zoom_window.hide()

            def get_primary_right():
                pm = engine.primary_monitor
                return pm["left"] + pm["width"]

            threading.Thread(
                target=_run_proximity_poll,
                args=(get_primary_right, bridge.hud_visibility_changed),
                daemon=True,
            ).start()

            threading.Thread(
                target=start_input_hooks,
                args=(bridge, engine),
                daemon=True,
            ).start()

            sys.exit(app.exec_())
    finally:
        # Safety: always restore standard mouse speed on exit (crash or normal quit)
        _set_mouse_speed(MOUSE_SPEED_STANDARD)


if __name__ == "__main__":
    import fire

    fire.Fire(main)
