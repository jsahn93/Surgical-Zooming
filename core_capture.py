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

from typing import Dict, Mapping, Tuple, Union

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


def compute_capture_dimensions(
    zoom_size: int,
    target_width: int,
    target_height: int,
) -> Tuple[int, int]:
    """Compute capture region width and height to match target display aspect ratio.

    Ensures the mss capture region matches the target secondary display's aspect
    ratio, preventing pillarboxing when the zoom window is full-screen.

    Args:
        zoom_size: Base dimension (used for the smaller side of the capture).
        target_width: Target display width in pixels.
        target_height: Target display height in pixels.

    Returns:
        Tuple of (capture_width, capture_height) matching target aspect ratio.
    """
    if target_height <= 0:
        return zoom_size, zoom_size
    aspect_ratio = target_width / target_height
    # Use height as base; derive width to preserve aspect
    capture_height = zoom_size
    capture_width = max(1, int(zoom_size * aspect_ratio))
    return capture_width, capture_height


def compute_centered_region(
    cursor_x: int,
    cursor_y: int,
    monitor: MonitorMapping,
    width: int,
    height: int,
) -> Region:
    """Compute a rectangular capture region centered on the cursor and clamped to a monitor.

    The region will always stay fully inside the given monitor's bounds, even
    when the cursor is near the edges. Dimensions may be non-square to match
    the target display aspect ratio (anti-pillarboxing).

    Args:
        cursor_x: Global X coordinate of the cursor.
        cursor_y: Global Y coordinate of the cursor.
        monitor: Monitor descriptor (e.g. from `mss.monitors[1]`).
        width: Capture region width in pixels.
        height: Capture region height in pixels.

    Returns:
        Dictionary describing the region with keys: ``left``, ``top``, ``width``, ``height``.
    """
    pm_left = monitor["left"]
    pm_top = monitor["top"]
    pm_right = pm_left + monitor["width"]
    pm_bottom = pm_top + monitor["height"]

    half_w = width // 2
    half_h = height // 2

    # Clamp the center so the capture region stays fully on the monitor
    cx = int(clamp(cursor_x, pm_left + half_w, pm_right - half_w))
    cy = int(clamp(cursor_y, pm_top + half_h, pm_bottom - half_h))

    left = cx - half_w
    top = cy - half_h

    return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


def capture_zoom_region(
    sct: mss.mss,
    cursor_x: int,
    cursor_y: int,
    monitor: MonitorMapping,
    width: int,
    height: int,
) -> ScreenShot:
    """Capture a cursor-centered rectangular region from the specified monitor.

    Args:
        sct: Active `mss` screen capture instance.
        cursor_x: Global X coordinate of the cursor.
        cursor_y: Global Y coordinate of the cursor.
        monitor: Monitor descriptor (typically the primary monitor).
        width: Capture region width in pixels.
        height: Capture region height in pixels.

    Returns:
        An `mss.base.ScreenShot` object representing the captured region.
    """
    region = compute_centered_region(cursor_x, cursor_y, monitor, width, height)
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

