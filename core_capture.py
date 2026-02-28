"""Core screen capture and zoom calculations for Surgical Zooming.

This module contains all non-GUI logic related to:
- Determining the primary monitor.
- Computing a cursor-centered capture region clamped to monitor bounds.
- Performing the actual screen capture with `mss`.
- Computing the zoom window size from configuration values.

Keeping this logic here satisfies the GUI-vs-core separation rule: the PyQt5
GUI is responsible only for display and user interaction, while this module
handles capture math and core behavior.
"""

from typing import Dict, Mapping, Union

import mss
from mss.base import ScreenShot

Number = Union[int, float]
MonitorMapping = Mapping[str, int]
Region = Dict[str, int]


def clamp(value: Number, minimum: Number, maximum: Number) -> Number:
    """Clamp a numeric value into the inclusive range [`minimum`, `maximum`].

    Args:
        value: Value to clamp.
        minimum: Lower bound.
        maximum: Upper bound.

    Returns:
        Clamped value.
    """

    return max(minimum, min(value, maximum))


def get_primary_monitor(sct: mss.mss) -> MonitorMapping:
    """Return the primary monitor descriptor from an `mss` instance.

    Args:
        sct: Active `mss` screen capture instance.

    Returns:
        Mapping with monitor geometry keys: ``left``, ``top``, ``width``, ``height``.
    """

    return sct.monitors[1]


def compute_centered_region(
    cursor_x: int,
    cursor_y: int,
    monitor: MonitorMapping,
    size: int,
) -> Region:
    """Compute a square capture region centered on the cursor and clamped to a monitor.

    The region will always stay fully inside the given monitor's bounds, even
    when the cursor is near the edges.

    Args:
        cursor_x: Global X coordinate of the cursor.
        cursor_y: Global Y coordinate of the cursor.
        monitor: Monitor descriptor (e.g. from `mss.monitors[1]`).
        size: Side length, in pixels, of the square capture region.

    Returns:
        Dictionary describing the region with keys: ``left``, ``top``, ``width``, ``height``.
    """

    pm_left = monitor["left"]
    pm_top = monitor["top"]
    pm_right = pm_left + monitor["width"]
    pm_bottom = pm_top + monitor["height"]

    half = size // 2

    # Clamp the center so the capture region stays fully on the monitor
    cx = int(clamp(cursor_x, pm_left + half, pm_right - half))
    cy = int(clamp(cursor_y, pm_top + half, pm_bottom - half))

    left = cx - half
    top = cy - half

    return {
        "left": left,
        "top": top,
        "width": size,
        "height": size,
    }


def capture_zoom_region(
    sct: mss.mss,
    cursor_x: int,
    cursor_y: int,
    monitor: MonitorMapping,
    size: int,
) -> ScreenShot:
    """Capture a cursor-centered square region from the specified monitor.

    Args:
        sct: Active `mss` screen capture instance.
        cursor_x: Global X coordinate of the cursor.
        cursor_y: Global Y coordinate of the cursor.
        monitor: Monitor descriptor (typically the primary monitor).
        size: Side length, in pixels, of the square capture region.

    Returns:
        An `mss.base.ScreenShot` object representing the captured region.
    """

    region = compute_centered_region(cursor_x, cursor_y, monitor, size)
    return sct.grab(region)


def compute_window_size(zoom_size: int, zoom_factor: float) -> int:
    """Compute the zoom window side length in pixels.

    Args:
        zoom_size: Base capture size (square side length in pixels).
        zoom_factor: Zoom multiplier applied when rendering in the window.

    Returns:
        Integer number of pixels for the window side length.
    """

    return int(zoom_size * zoom_factor)

