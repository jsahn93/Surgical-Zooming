"""PyQt5-based remote desktop zoom tool.

This module wires together the core capture engine and the PyQt5 zoom
window, and exposes a CLI-friendly `main()` entry point. Global hotkeys
and input hooks (Capslock dual-mode, Ctrl+Scroll zoom) run on background
threads and communicate with the GUI only via PyQt signals to avoid
thread contention and frame drops.
"""

import atexit
import json
import logging
import os
import sys
import threading
import time
from ctypes import byref, c_int, windll

from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse
from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal, QPoint
from PyQt5.QtGui import QCursor, QScreen
from PyQt5.QtWidgets import QApplication

from core.capture_engine import CaptureEngine
from gui.zoom_window import ZoomWindow
from input_hooks import start_ctrl_scroll_hook

LOGGER = logging.getLogger(__name__)

# Windows mouse speed API (Precision Mode)
SPI_SETMOUSESPEED = 0x0071
MOUSE_SPEED_STANDARD = 10
MOUSE_SPEED_PRECISION = 4

# =========================
# Configuration
# =========================

ZOOM_SIZE: int = 300
ZOOM_FACTOR: float = 2.0
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


SETTINGS_PATH = resolve_resource_path("settings.json")


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


# Safety: always try to restore standard mouse speed on normal interpreter exit.
atexit.register(lambda: _set_mouse_speed(MOUSE_SPEED_STANDARD))


class SettingsManager:
    """Load/save persistent settings next to the executable (PyInstaller-safe)."""

    def __init__(
        self,
        monitor_index: int | None = None,
        zoom_factor: float = 2.0,
        toggle_bind: str = "ctrl+caps_lock",
        precision_mode: bool = True,
        path: str = SETTINGS_PATH,
    ) -> None:
        self.monitor_index = monitor_index
        self.zoom_factor = zoom_factor
        self.toggle_bind = toggle_bind
        self.precision_mode = precision_mode
        self._path = path

    @classmethod
    def load(cls) -> "SettingsManager":
        """Load settings from JSON, falling back to safe defaults."""
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return cls()
        except Exception:
            # Corrupt or unreadable settings; start fresh.
            return cls()

        monitor_index = data.get("monitor_index")
        zoom_factor = float(data.get("zoom_factor", 2.0))
        toggle_bind = str(data.get("toggle_bind", "ctrl+caps_lock"))
        precision_mode = bool(data.get("precision_mode", True))
        return cls(
            monitor_index=monitor_index,
            zoom_factor=zoom_factor,
            toggle_bind=toggle_bind,
            precision_mode=precision_mode,
        )

    def save(self) -> None:
        payload = {
            "monitor_index": self.monitor_index,
            "zoom_factor": self.zoom_factor,
            "toggle_bind": self.toggle_bind,
            "precision_mode": self.precision_mode,
        }
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        except Exception:
            # Directory might not be creatable (e.g. root); ignore.
            pass
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            # Persistence failure should never crash the app.
            logging.getLogger(__name__).warning(
                "Failed to save settings.json", exc_info=True
            )


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
    toggle_bind_changed = pyqtSignal(str)
    quit_requested = pyqtSignal()

    def __init__(
        self,
        initial_zoom_factor: float,
        zoom_min: float = 0.5,
        zoom_max: float = 4.0,
        initial_precision_mode: bool = True,
        initial_toggle_bind: str = "ctrl+caps_lock",
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
        self._precision_mode = bool(initial_precision_mode)
        self._toggle_bind = initial_toggle_bind
        self._is_rebinding = False
        self._pressed_combo: set[str] = set()
        self._high_water_mark: set[str] = set()

    @property
    def is_rebinding(self) -> bool:
        with self._lock:
            return self._is_rebinding

    @is_rebinding.setter
    def is_rebinding(self, value: bool) -> None:
        with self._lock:
            self._is_rebinding = bool(value)

    @property
    def is_precision_mode(self) -> bool:
        with self._lock:
            return self._precision_mode

    def set_precision_mode(self, value: bool) -> None:
        with self._lock:
            self._precision_mode = value
        self.precision_mode_changed.emit(self._precision_mode)

    @property
    def toggle_bind(self) -> str:
        with self._lock:
            return self._toggle_bind

    def set_toggle_bind(self, value: str) -> None:
        with self._lock:
            if self._toggle_bind == value:
                return
            self._toggle_bind = value
        self.toggle_bind_changed.emit(value)

    def start_rebinding(self) -> None:
        """Enter rebinding mode; next combo of inputs updates the toggle bind.

        This method also clears any previously tracked combo state so that the
        next non-empty "high-water" combination of keys/buttons becomes the
        new binding once all inputs are released.
        """
        with self._lock:
            self._is_rebinding = True
            self._pressed_combo.clear()
            self._high_water_mark.clear()

    def cancel_rebinding(self) -> None:
        """Exit rebinding mode without changing the current toggle bind."""
        self.is_rebinding = False

    def update_combo_state(self, token: str, pressed: bool) -> tuple[set[str], bool]:
        """Update the currently pressed combo set and high-water mark.

        Args:
            token: Normalized string for a key or mouse button (for example,
                ``\"ctrl\"``, ``\"caps_lock\"``, or ``\"button.x1\"``).
            pressed: ``True`` when the input is pressed, ``False`` when
                released.

        Returns:
            A tuple of:

            * A snapshot of the currently pressed combo set.
            * A boolean indicating whether the bridge is currently in
              rebinding mode.
        """
        with self._lock:
            if pressed:
                self._pressed_combo.add(token)
                if len(self._pressed_combo) > len(self._high_water_mark):
                    self._high_water_mark = set(self._pressed_combo)
            else:
                self._pressed_combo.discard(token)

            current = set(self._pressed_combo)
            is_rebinding = self._is_rebinding

        return current, is_rebinding

    def finalize_rebinding_if_idle(self) -> None:
        """Commit the high-water combo when all inputs are released.

        When in rebinding mode and the currently pressed combo set becomes
        empty, this method converts the recorded high-water mark into a
        ``\"+\"``-joined toggle binding string and updates ``toggle_bind``.
        """
        with self._lock:
            if not self._is_rebinding:
                return
            if self._pressed_combo:
                return
            if not self._high_water_mark:
                self._is_rebinding = False
                return

            parts = sorted(self._high_water_mark)
            self._is_rebinding = False

        bind_str = "+".join(parts)
        self.set_toggle_bind(bind_str)

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
    """Register global input hooks and delegate zoom toggling via the bridge.

    Keyboard and mouse listeners share a single logical "currently pressed"
    set so that combinations like ``\"ctrl+caps_lock\"`` or
    ``\"shift+button.x2\"`` can be used both for normal operation and
    high-water rebinding.
    """

    def quit_app() -> None:
        bridge.quit_requested.emit()

    def _on_toggle_press() -> None:
        """Simple toggle: flip active state on each trigger press."""
        new_state = not bridge.is_active
        bridge._set_active(new_state)
        bridge.active_changed.emit(new_state)

    def _key_to_string(key: pynput_keyboard.Key | pynput_keyboard.KeyCode) -> str:
        """Normalize pynput key objects to a stable, lowercased string.

        This is defensive against odd pynput objects: we prefer an explicit
        ``name`` attribute (for special keys like Caps Lock or Shift), then a
        ``char`` attribute (for alphanumeric keys). As a final fallback we use
        ``str(key)`` and strip common prefixes like ``Key.``. Any failure is
        logged at debug level and returns a best-effort string so that
        background listener threads never crash.
        """
        try:
            # Explicit special case: ensure spacebar always normalizes to "space"
            if key == pynput_keyboard.Key.space:
                return "space"

            # Prefer an explicit .name (special keys, e.g. Key.caps_lock)
            name = getattr(key, "name", None)
            if isinstance(name, str) and name:
                name = name.lower()
                # Normalize left/right variants to a single logical modifier.
                if name in {"ctrl_l", "ctrl_r", "ctrl"}:
                    return "ctrl"
                if name in {"shift_l", "shift_r", "shift"}:
                    return "shift"
                if name in {"alt_l", "alt_r", "alt"}:
                    return "alt"
                if name in {"cmd", "cmd_l", "cmd_r", "win_l", "win_r"}:
                    return "cmd"
                return name

            # Then prefer a character payload (KeyCode or similar)
            char = getattr(key, "char", None)
            if isinstance(char, str) and char:
                return char.lower()

            text = str(key)
            if text.startswith("Key."):
                return text.split(".", 1)[1].lower()
            return text.lower()
        except Exception:
            LOGGER.debug("Failed to normalize key %r", key, exc_info=True)
            return str(key)

    def _button_to_string(button: pynput_mouse.Button) -> str:
        """Normalize pynput mouse button objects to a stable, lowercased string.

        The stored representation uses the ``button.<name>`` pattern (for
        example: ``button.x1``, ``button.middle``) to align with the toggle
        binding string format. Any unexpected object shape is handled
        defensively and logged at debug level.
        """
        try:
            name = getattr(button, "name", None)
            if isinstance(name, str) and name:
                return f"button.{name.lower()}"

            text = str(button)
            if text.startswith("Button."):
                return "button." + text.split(".", 1)[1].lower()
            return text.lower()
        except Exception:
            LOGGER.debug("Failed to normalize mouse button %r", button, exc_info=True)
            return str(button)

    def _parse_toggle_bind(bind: str) -> set[str]:
        """Split a ``\"+\"``-joined toggle binding string into a combo set."""
        return {part for part in bind.split("+") if part}

    def _maybe_trigger_combo_toggle(
        token: str,
        pressed: bool,
    ) -> None:
        """Update combo state and toggle when it exactly matches the binding.

        Normal operation:

        * On each press, update the shared pressed set and, when not rebinding,
          compare it against the current binding combo. If they are a non-empty
          exact match, trigger the zoom toggle.
        * On release, only update combo state (no toggling) so that the
          high-water logic can detect when all inputs are released.
        """
        current_combo, is_rebinding = bridge.update_combo_state(token, pressed)

        # During rebinding we only track high-water combos and wait for idle.
        if is_rebinding:
            if not pressed:
                bridge.finalize_rebinding_if_idle()
            return

        # Outside of rebinding, toggles are only evaluated on presses.
        if not pressed:
            return

        bind_combo = _parse_toggle_bind(bridge.toggle_bind)
        if not bind_combo:
            return

        if current_combo == bind_combo:
            _on_toggle_press()

    def on_key_press(key: pynput_keyboard.Key | pynput_keyboard.KeyCode) -> None:
        try:
            key_str = _key_to_string(key)

            if key == pynput_keyboard.Key.esc:
                quit_app()
                return

            _maybe_trigger_combo_toggle(key_str, True)
        except Exception:
            # Never propagate exceptions from background listener threads.
            LOGGER.debug("Exception in keyboard on_press callback", exc_info=True)
            return

    def on_key_release(key: pynput_keyboard.Key | pynput_keyboard.KeyCode) -> None:
        try:
            key_str = _key_to_string(key)
            _maybe_trigger_combo_toggle(key_str, False)
        except Exception:
            LOGGER.debug("Exception in keyboard on_release callback", exc_info=True)
            return

    def on_click(x: int, y: int, button: pynput_mouse.Button, pressed: bool) -> None:
        try:
            # Intuitive guardrail: never allow Left or Right click to
            # participate in combo binding or triggering.
            if button in (pynput_mouse.Button.left, pynput_mouse.Button.right):
                return

            button_str = _button_to_string(button)
            _maybe_trigger_combo_toggle(button_str, pressed)
        except Exception:
            LOGGER.debug("Exception in mouse on_click callback", exc_info=True)
            return

    def get_primary_bounds() -> tuple[int, int, int, int]:
        pm = engine.primary_monitor
        return pm["left"], pm["top"], pm["width"], pm["height"]

    def on_zoom_delta(delta: int) -> None:
        # Emit to main thread; bridge slot will throttle
        bridge.zoom_delta_requested.emit(delta)

    # Ctrl+Scroll zoom remains implemented via the low-level Windows hook.
    start_ctrl_scroll_hook(
        get_is_active=lambda: bridge.is_active,
        get_primary_bounds=get_primary_bounds,
        on_zoom_delta=on_zoom_delta,
    )

    # Run keyboard and mouse listeners in the background; callbacks only emit signals.
    keyboard_listener = pynput_keyboard.Listener(
        on_press=on_key_press,
        on_release=on_key_release,
    )
    mouse_listener = pynput_mouse.Listener(on_click=on_click)

    keyboard_listener.start()
    mouse_listener.start()

    # Block this daemon thread until both listeners stop (process exit).
    keyboard_listener.join()
    mouse_listener.join()


def main(monitor_index: int | None = None) -> None:
    """Entry point. Window starts hidden (Glass Desktop); activate via combo bind.

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

    settings = SettingsManager.load()

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
        selected_index: int | None = None
    else:
        screen_count = len(screens)
        # Determine effective monitor index: CLI arg wins, then persisted settings, else last screen.
        effective_index = monitor_index
        if effective_index is None:
            effective_index = settings.monitor_index

        if effective_index is None:
            selected_index = screen_count - 1
        else:
            if effective_index < 0 or effective_index >= screen_count:
                logger.warning(
                    "Requested monitor_index %d out of range [0, %d); "
                    "defaulting to last screen.",
                    effective_index,
                    screen_count,
                )
                selected_index = screen_count - 1
            else:
                selected_index = effective_index

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

        # Persist the final monitor choice.
        settings.monitor_index = selected_index
        settings.save()

    bridge = StateBridge(
        initial_zoom_factor=settings.zoom_factor,
        zoom_min=1.5,
        zoom_max=5.0,
        initial_precision_mode=settings.precision_mode,
        initial_toggle_bind=settings.toggle_bind,
    )
    logger = logging.getLogger(__name__)

    def update_mouse_speed() -> None:
        """Set system mouse speed: 4 (slow) when Precision Mode + Active, else 10 (standard)."""
        use_slow = bridge.is_precision_mode and bridge.is_active
        _set_mouse_speed(MOUSE_SPEED_PRECISION if use_slow else MOUSE_SPEED_STANDARD)

    def persist_zoom_factor(value: float) -> None:
        settings.zoom_factor = value
        settings.save()

    def persist_precision_mode(value: bool) -> None:
        settings.precision_mode = value
        settings.save()

    def persist_toggle_bind(value: str) -> None:
        settings.toggle_bind = value
        settings.save()

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
                    # Force-Off Yield: ensure keyboard toggle state is normalized when deactivating.
                    normalize_keyboard_state()
                update_mouse_speed()

            def on_zoom_factor_changed(value: float) -> None:
                zoom_window.set_zoom_factor(value)
                persist_zoom_factor(value)

            def on_precision_mode_changed(value: bool) -> None:
                update_mouse_speed()
                persist_precision_mode(value)

            bridge.active_changed.connect(on_active_changed)
            bridge.zoom_factor_changed.connect(on_zoom_factor_changed)
            bridge.quit_requested.connect(app.quit)
            bridge.zoom_delta_requested.connect(bridge.on_zoom_delta)
            bridge.precision_mode_changed.connect(on_precision_mode_changed)
            bridge.toggle_bind_changed.connect(persist_toggle_bind)

            zoom_window.hide()

            if target_screen is not None:
                def _poll_hud_proximity() -> None:
                    """GUI-thread proximity poll: emit HUD visibility when cursor is on target screen.

                    Uses Qt's global cursor position and the selected QScreen geometry so the
                    proximity logic is agnostic to Windows monitor arrangement.
                    """
                    try:
                        pos: QPoint = QCursor.pos()
                        geom = target_screen.geometry()
                        contains = geom.contains(pos)
                        logger.debug(
                            "HUD proximity poll - Mouse X: %d, Y: %d, "
                            "Target Screen Bounds: x=%d, y=%d, w=%d, h=%d, contains=%s",
                            pos.x(),
                            pos.y(),
                            geom.x(),
                            geom.y(),
                            geom.width(),
                            geom.height(),
                            contains,
                        )
                        bridge.hud_visibility_changed.emit(contains)
                    except Exception:
                        logger.exception("Error in HUD proximity poll")

                proximity_timer = QTimer(zoom_window)
                proximity_timer.setInterval(PROXIMITY_POLL_MS)
                proximity_timer.timeout.connect(_poll_hud_proximity)
                proximity_timer.start()

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
