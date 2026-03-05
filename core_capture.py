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


def is_cursor_on_primary(cursor_x: int, cursor_y: int, monitor: MonitorMapping) -> bool:
    """Return True if the cursor is within the primary monitor bounds.

    Used to gate Ctrl+Scroll zoom (only when cursor is on primary) and
    Phase 3 proximity HUD (cursor on secondary = show HUD).

    Args:
        cursor_x: Global X coordinate of the cursor.
        cursor_y: Global Y coordinate of the cursor.
        monitor: Monitor descriptor (e.g. from mss.monitors[1]).

    Returns:
        True if (cursor_x, cursor_y) is inside the monitor rectangle.
    """
    left, top = monitor["left"], monitor["top"]
    right = left + monitor["width"]
    bottom = top + monitor["height"]
    return left <= cursor_x < right and top <= cursor_y < bottom


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


def logical_to_physical_cursor(
    logical_x: Number,
    logical_y: Number,
    device_pixel_ratio: float,
) -> Tuple[int, int]:
    """Convert logical (Qt) cursor coordinates to physical pixels for mss.

    PyQt5 reports cursor and screen geometry in logical (device-independent)
    coordinates. mss uses physical pixels. Multiplying by the primary screen's
    device pixel ratio aligns the two coordinate systems.

    Args:
        logical_x: Cursor X in logical coordinates.
        logical_y: Cursor Y in logical coordinates.
        device_pixel_ratio: Primary screen's devicePixelRatio() (e.g. from QScreen).

    Returns:
        (physical_x, physical_y) for use with mss capture regions.
    """
    physical_x = int(logical_x * device_pixel_ratio)
    physical_y = int(logical_y * device_pixel_ratio)
    return physical_x, physical_y


def compute_centered_region(
    cursor_x: int,
    cursor_y: int,
    monitor: MonitorMapping,
    width: int,
    height: int,
    device_pixel_ratio: float = 1.0,
) -> Region:
    """Compute a rectangular capture region centered on the cursor and clamped to a monitor.

    The region will always stay fully inside the given monitor's bounds, even
    when the cursor is near the edges. Dimensions may be non-square to match
    the target display aspect ratio (anti-pillarboxing). Applied after DPI
    correction so capture size and aspect ratio are in physical pixels.

    Cursor coordinates are interpreted as logical when device_pixel_ratio != 1.0;
    they are converted to physical using the primary monitor's DPR before
    computing the region. Monitor geometry from mss is always in physical pixels.

    Monitor-relative clamping: the primary monitor's left/top are subtracted so
    that centering and clamping are done in primary-relative space, then the
    region is mapped back to global physical coordinates for mss. This keeps
    the math correct regardless of virtual display arrangement (e.g. VDD
    logically above or left of the main screen).

    Args:
        cursor_x: Global X coordinate of the cursor (logical if device_pixel_ratio != 1).
        cursor_y: Global Y coordinate of the cursor (logical if device_pixel_ratio != 1).
        monitor: Primary monitor descriptor in physical pixels (e.g. from `mss.monitors[1]`).
        width: Capture region width in physical pixels.
        height: Capture region height in physical pixels.
        device_pixel_ratio: Primary screen DPR; when not 1.0, cursor_x/y are logical.

    Returns:
        Dictionary describing the region with keys: ``left``, ``top``, ``width``, ``height``.
    """
    # Logical → physical cursor (align PyQt5 and mss coordinate systems)
    phys_x, phys_y = logical_to_physical_cursor(
        cursor_x, cursor_y, device_pixel_ratio
    )

    # Primary monitor geometry in physical pixels (mss)
    pm_left = monitor["left"]
    pm_top = monitor["top"]
    pm_width = monitor["width"]
    pm_height = monitor["height"]

    # Monitor-relative cursor and bounds for clamping
    rel_x = phys_x - pm_left
    rel_y = phys_y - pm_top

    half_w = width // 2
    half_h = height // 2
    rel_right = pm_width - half_w
    rel_bottom = pm_height - half_h
    # Ensure clamp range is valid (e.g. when capture is larger than monitor)
    cx_max = max(half_w, rel_right)
    cy_max = max(half_h, rel_bottom)

    # Clamp center in primary-relative space so region stays fully on primary
    cx_rel = int(clamp(rel_x, half_w, cx_max))
    cy_rel = int(clamp(rel_y, half_h, cy_max))

    rel_left = cx_rel - half_w
    rel_top = cy_rel - half_h

    # Map region back to global physical coordinates for mss
    left = rel_left + pm_left
    top = rel_top + pm_top

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
    device_pixel_ratio: float = 1.0,
) -> ScreenShot:
    """Capture a cursor-centered rectangular region from the specified monitor.

    Args:
        sct: Active `mss` screen capture instance.
        cursor_x: Global X coordinate of the cursor (logical if device_pixel_ratio != 1).
        cursor_y: Global Y coordinate of the cursor (logical if device_pixel_ratio != 1).
        monitor: Monitor descriptor in physical pixels (typically the primary monitor).
        width: Capture region width in physical pixels.
        height: Capture region height in physical pixels.
        device_pixel_ratio: Primary screen DPR for logical→physical cursor conversion.

    Returns:
        An `mss.base.ScreenShot` object representing the captured region.
    """
    region = compute_centered_region(
        cursor_x, cursor_y, monitor, width, height,
        device_pixel_ratio=device_pixel_ratio,
    )
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

