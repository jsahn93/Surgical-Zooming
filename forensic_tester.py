import logging
import random
import sys
import threading
import time
from collections import deque
from ctypes import windll

from pynput import keyboard as pynput_keyboard


LOGGER = logging.getLogger(__name__)


VK_CAPITAL = 0x14


def _get_capslock_state() -> bool:
    """Return True if Caps Lock is ON according to the OS (Windows-only)."""
    if sys.platform != "win32":
        return False
    try:
        # Low-order bit set means toggle state ON.
        return bool(windll.user32.GetKeyState(VK_CAPITAL) & 1)
    except Exception:
        LOGGER.exception("Failed to query Caps Lock state via GetKeyState")
        return False


def _stress_sequence(
    controller: pynput_keyboard.Controller,
    toggle_key: pynput_keyboard.Key,
    seed: int | None = None,
    iterations: int = 100,
    delay_min_ms: int = 5,
    delay_max_ms: int = 40,
) -> None:
    """Run a randomized stress sequence of toggle + mixed keys.

    This is intentionally noisy: it sends rapid mixes of the toggle key,
    whitespace, modifiers, and alphanumerics to try to surface race
    conditions in the main application's pynput listener.

    Args:
        controller: Shared keyboard controller instance.
        toggle_key: The key object representing the toggle binding
            (typically Key.caps_lock).
        seed: Optional RNG seed for reproducibility.
        iterations: Number of randomized strokes to send.
        delay_min_ms: Minimum inter-key delay in milliseconds.
        delay_max_ms: Maximum inter-key delay in milliseconds.
    """
    rng = random.Random(seed)

    key_pool: list[object] = [
        toggle_key,
        pynput_keyboard.Key.space,
        pynput_keyboard.Key.enter,
        pynput_keyboard.Key.shift,
        pynput_keyboard.Key.shift_r,
        pynput_keyboard.Key.alt,
        pynput_keyboard.Key.alt_gr,
        pynput_keyboard.Key.tab,
        pynput_keyboard.Key.esc,
    ]

    # Add some alphanumerics (KeyCode instances)
    for ch in "asdfghjklQWERTY123 ":
        key_pool.append(pynput_keyboard.KeyCode.from_char(ch))

    recent_sent = deque(maxlen=20)

    for idx in range(iterations):
        key = rng.choice(key_pool)
        recent_sent.append(key)

        try:
            controller.press(key)
            controller.release(key)
        except Exception:
            LOGGER.exception("Failed to send synthetic key %r", key)

        # Short, randomized inter-key delay
        delay_ms = rng.randint(delay_min_ms, delay_max_ms)
        time.sleep(delay_ms / 1000.0)

        # After each key, probe OS Caps Lock state
        caps_on = _get_capslock_state()
        if caps_on:
            LOGGER.warning(
                "Caps Lock observed ON during stress sequence at step %d; "
                "recent synthetic keys: %s",
                idx,
                list(recent_sent),
            )
            # Best-effort correction pulse to restore OFF for continued testing.
            try:
                controller.press(toggle_key)
                controller.release(toggle_key)
            except Exception:
                LOGGER.exception("Failed to send corrective Caps Lock pulse")


def _monitor_listener_health(
    poll_interval_sec: float = 1.0,
    duration_sec: float = 60.0,
) -> None:
    """Heuristic monitor for listener health based on OS Caps Lock state.

    From outside the main process we cannot directly inspect its
    pynput.Listener threads. This monitor instead watches for unexpected
    Caps Lock flips that persist, which strongly suggests that the hook is
    no longer suppressing or correcting toggle events.

    Args:
        poll_interval_sec: Sleep interval between probes.
        duration_sec: Total monitoring duration.
    """
    if sys.platform != "win32":
        LOGGER.info("Listener health monitor is Windows-only; skipping.")
        return

    start = time.monotonic()
    last_state = _get_capslock_state()
    while time.monotonic() - start < duration_sec:
        time.sleep(poll_interval_sec)
        state = _get_capslock_state()
        if state != last_state:
            LOGGER.warning(
                "Observed Caps Lock state transition during monitoring: %s -> %s "
                "(this may indicate listener desync or OS-level ghost toggle).",
                last_state,
                state,
            )
            last_state = state


def run_forensic_stress(
    iterations: int = 500,
    seed: int | None = None,
    monitor_duration_sec: float = 90.0,
) -> None:
    """Run an autonomous forensic stress test against the toggle key.

    This function is designed to be executed *while the main Surgical
    Zooming application is running*. It sends randomized key sequences
    (including Caps Lock) and reports:

    - Any time the OS-level Caps Lock state flips ON.
    - Any persistent state changes observed by the monitor thread.

    Args:
        iterations: Number of synthetic key events to send.
        seed: Optional random seed to reproduce a problematic sequence.
        monitor_duration_sec: Time window for post-stress monitoring.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    LOGGER.info("Starting forensic Caps Lock stress test.")

    if sys.platform != "win32":
        LOGGER.warning(
            "Forensic stress test is tuned for Windows; current platform=%s",
            sys.platform,
        )

    controller = pynput_keyboard.Controller()
    toggle_key = pynput_keyboard.Key.caps_lock

    # Normalize to a known OFF baseline before starting.
    if _get_capslock_state():
        LOGGER.info("Caps Lock initially ON; sending corrective pulse to start from OFF.")
        try:
            controller.press(toggle_key)
            controller.release(toggle_key)
        except Exception:
            LOGGER.exception("Failed to normalize initial Caps Lock state")

    monitor_thread = threading.Thread(
        target=_monitor_listener_health, kwargs={"duration_sec": monitor_duration_sec}, daemon=True
    )
    monitor_thread.start()

    _stress_sequence(
        controller=controller,
        toggle_key=toggle_key,
        seed=seed,
        iterations=iterations,
    )

    LOGGER.info("Stress sequence complete; continuing to monitor for %.1f seconds.", monitor_duration_sec)
    monitor_thread.join(timeout=monitor_duration_sec + 5.0)
    LOGGER.info("Forensic Caps Lock stress test finished.")


if __name__ == "__main__":
    import fire

    fire.Fire(run_forensic_stress)

