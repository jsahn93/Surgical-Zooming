"""Capture engine and zoom math for Surgical Zooming.

This module encapsulates all `mss`-based screen capture and
cursor-centered zoom coordinate calculations, decoupled from any GUI code.
"""

from __future__ import annotations

import logging
from typing import Dict, Mapping, Union

import mss
from mss.base import ScreenShot

from core_capture import compute_centered_region, get_primary_monitor

Number = Union[int, float]
MonitorMapping = Mapping[str, int]
Region = Dict[str, int]

LOGGER = logging.getLogger(__name__)


class CaptureEngine:
    """Core capture engine built on top of `mss`.

    This class owns the `mss` instance and provides high-level operations for
    capturing a cursor-centered region on the primary monitor. It is designed
    to be used as a context manager so that native resources held by `mss`
    are cleaned up deterministically.

    Example:
        with CaptureEngine() as engine:
            screenshot = engine.capture_cursor_region(cursor_x, cursor_y, size)
    """

    def __init__(self) -> None:
        """Initialize a new `CaptureEngine` with an internal `mss` instance."""
        self._sct: mss.mss = mss.mss()
        self._primary_monitor: MonitorMapping = get_primary_monitor(self._sct)
        LOGGER.debug(
            "Initialized CaptureEngine with primary monitor: %s",
            self._primary_monitor,
        )

    @property
    def primary_monitor(self) -> MonitorMapping:
        """Return the primary monitor descriptor."""
        return self._primary_monitor

    def capture_cursor_region(
        self, cursor_x: int, cursor_y: int, capture_width: int, capture_height: int
    ) -> ScreenShot:
        """Capture a cursor-centered rectangular region from the primary monitor.

        The region dimensions should match the target display aspect ratio to
        prevent pillarboxing when the zoom window is full-screen.

        Args:
            cursor_x: Global X coordinate of the cursor.
            cursor_y: Global Y coordinate of the cursor.
            capture_width: Capture region width in pixels.
            capture_height: Capture region height in pixels.

        Returns:
            An `mss.base.ScreenShot` representing the captured region.
        """
        region: Region = compute_centered_region(
            cursor_x, cursor_y, self._primary_monitor, capture_width, capture_height
        )
        LOGGER.debug("Capturing cursor-centered region: %s", region)
        return self._sct.grab(region)

    def close(self) -> None:
        """Release any native resources held by the underlying `mss` instance."""
        LOGGER.debug("Closing CaptureEngine and releasing mss resources.")
        self._sct.close()

    def __enter__(self) -> CaptureEngine:
        """Enter the managed context and return the engine instance."""
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit the managed context and close the underlying `mss` instance."""
        self.close()
