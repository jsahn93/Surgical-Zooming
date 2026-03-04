"""Global input hooks for Surgical Zooming (Phase 2).

Provides Ctrl+MouseWheel zoom adjustment. Uses ctypes + Windows low-level
mouse hook (no new dependencies). Only active when IS_ACTIVE and cursor
is on the primary monitor. Compatible with 64-bit Python 3.11+.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
import threading
from typing import Callable

LOGGER = logging.getLogger(__name__)

# Windows constants
WH_MOUSE_LL = 14
WM_MOUSEWHEEL = 0x020A
VK_CONTROL = 0x11
HC_ACTION = 0

# Pointer-sized types for 64-bit Windows (Python 3.11 64-bit)
if sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8:
    WPARAM = ctypes.c_ulonglong
    LPARAM = ctypes.c_ulonglong
    LRESULT = ctypes.c_ulonglong
    HINSTANCE = ctypes.c_void_p
    HHOOK = ctypes.c_void_p
else:
    WPARAM = ctypes.c_ulong
    LPARAM = ctypes.c_ulong
    LRESULT = ctypes.c_long
    HINSTANCE = ctypes.c_void_p
    HHOOK = ctypes.c_void_p


class MSLLHOOKSTRUCT(ctypes.Structure):
    """Low-level mouse hook structure (MSLLHOOKSTRUCT)."""

    _fields_ = [
        ("pt", ctypes.wintypes.POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


def _run_scroll_hook(
    get_is_active: Callable[[], bool],
    get_primary_bounds: Callable[[], tuple[int, int, int, int]],
    on_zoom_delta: Callable[[int], None],
) -> None:
    """Run the Windows low-level mouse hook (blocks; run in dedicated thread)."""
    if sys.platform != "win32":
        LOGGER.debug("Ctrl+Scroll hook skipped (non-Windows)")
        return

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Ensure 64-bit-safe prototypes so SetWindowsHookExW succeeds
    kernel32.GetModuleHandleW.restype = HINSTANCE
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

    user32.SetWindowsHookExW.restype = HHOOK
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        HINSTANCE,
        ctypes.c_ulong,
    ]
    user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, WPARAM, LPARAM]
    user32.CallNextHookEx.restype = LRESULT

    hook_ref: list = [None]

    def low_level_mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
        hid = hook_ref[0]
        if hid is None:
            return 0
        if n_code != HC_ACTION or w_param != WM_MOUSEWHEEL:
            return user32.CallNextHookEx(hid, n_code, w_param, l_param)

        if not get_is_active():
            return user32.CallNextHookEx(hid, n_code, w_param, l_param)

        try:
            struct_ptr = ctypes.cast(
                ctypes.c_void_p(l_param), ctypes.POINTER(MSLLHOOKSTRUCT)
            )
            pt = struct_ptr.contents.pt
            x, y = pt.x, pt.y
        except (ValueError, TypeError):
            return user32.CallNextHookEx(hid, n_code, w_param, l_param)

        left, top, width, height = get_primary_bounds()
        if not (left <= x < left + width and top <= y < top + height):
            return user32.CallNextHookEx(hid, n_code, w_param, l_param)

        if (user32.GetKeyState(VK_CONTROL) & 0x8000) == 0:
            return user32.CallNextHookEx(hid, n_code, w_param, l_param)

        delta = ctypes.c_short(struct_ptr.contents.mouseData >> 16).value
        on_zoom_delta(delta)
        # Consume the event so the underlying app (browser, IDE) does not zoom/scroll
        return 1

    CMPFUNC = ctypes.CFUNCTYPE(
        ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    callback = CMPFUNC(low_level_mouse_proc)

    h_mod = kernel32.GetModuleHandleW(None)
    hook_ref[0] = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, h_mod, 0)
    if not hook_ref[0]:
        err = ctypes.get_last_error()
        LOGGER.warning(
            "Failed to install Ctrl+Scroll mouse hook (GetLastError=%s)", err
        )
        return

    LOGGER.info("Ctrl+Scroll mouse hook installed")
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWindowsHookEx(hook_ref[0])


def start_ctrl_scroll_hook(
    get_is_active: Callable[[], bool],
    get_primary_bounds: Callable[[], tuple[int, int, int, int]],
    on_zoom_delta: Callable[[int], None],
) -> threading.Thread:
    """Start the Ctrl+Scroll zoom hook in a daemon thread.

    Args:
        get_is_active: Callable returning bool – only process when True.
        get_primary_bounds: Callable returning (left, top, width, height).
        on_zoom_delta: Callable(delta: int) – positive = zoom in, negative = zoom out.

    Returns:
        The thread (daemon); caller must keep references so it stays alive.
    """
    thread = threading.Thread(
        target=_run_scroll_hook,
        args=(get_is_active, get_primary_bounds, on_zoom_delta),
        daemon=True,
    )
    thread.start()
    return thread
