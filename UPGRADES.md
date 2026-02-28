# Surgical Zooming Upgrade Protocol

**Aesthetic:** Industrial, Snappy, and Clinical. No bloat, no standard Windows UI clutter.

**Constraint:** Do not alter the core coordinate clamping that prevents "zoomception"; build *on top* of it.

---

## Phase 1: Visual Stability (The Fixes)

### 1.1 Enforce Aspect Ratio Lock (Anti-Pillarboxing)
**Status:** ✅ Implemented

**Logic:** The PyQt5 window on the secondary display is full-screen, but the `mss` capture region is currently yielding a mismatched aspect ratio, causing pillarboxing.

**Action:** Update the math in `core_capture.py`. The capture bounding box must strictly calculate its dimensions to match the aspect ratio of the target secondary display before passing the coordinates to `mss`.

**Files:** `core_capture.py`, `core/capture_engine.py` (if region computation flows through there), `gui/zoom_window.py` (target display aspect ratio must be known)

---

### 1.2 Synthetic Cursor Overlay
**Status:** ✅ Implemented

**Logic:** `mss` captures the frame buffer, deliberately bypassing the hardware cursor.

**Action:** Implement a synthetic cursor in `gui/zoom_window.py`. Draw a static, minimalist crosshair or default cursor icon perfectly centered on the PyQt5 canvas. Because the capture region always tracks the hardware mouse center, this static center-point will perfectly mimic the real cursor without adding input lag.

**Files:** `gui/zoom_window.py`

---

## Phase 2: Input & State Management (Functional Upgrades)

### 2.1 The Tactile Toggle State Machine (Capslock)
**Status:** ⬜ Pending

**Logic:** The activation key (default: Capslock) must support dual-mode intent.

**Action:** Implement a state machine using the `keyboard` library that measures the time delta between `key_down` and `key_up`.
- **Tap** (< 200ms) = Toggle state (On/Off).
- **Hold** (> 200ms) = Momentary state (Activates on `down`, deactivates on `up`).

**Files:** `main.py` (or hotkey module)

---

### 2.2 The "Glass Desktop" Yield
**Status:** ⬜ Pending

**Logic:** When Surgical Zoom is inactive, it must not hog the secondary display with a blank or frozen canvas.

**Action:** On deactivate, trigger PyQt5's `.hide()` method to completely drop the application from the rendering stack, revealing any underlying applications (e.g., Discord, terminals). On activate, trigger `.showFullScreen()`.

**Files:** `main.py`, `gui/zoom_window.py`

---

### 2.3 Isolated Zoom Ratio Adjustment (Ctrl + Scroll)
**Status:** ⬜ Pending

**Logic:** Global hooks for `Ctrl + Scroll` will cause critical interference with primary creative applications.

**Action:** Implement a conditional event listener. The `Ctrl + Scroll` logic to adjust the zoom multiplier must *only* register if:
1. The Surgical Zoom state is **ACTIVE**.
2. The cursor coordinates are currently within the **Primary Monitor** bounds.

**Files:** `main.py`, `gui/zoom_window.py` (zoom_factor state), `core_capture.py` or capture engine

---

## Phase 3: The Proximity HUD (GUI Evolution)

### 3.1 Contextual State Transition – Proximity HUD
**Status:** ⬜ Pending

**The Core Problem:** When the cursor crosses onto the secondary display, `core_capture.py` intentionally clamps the capture region to the primary monitor's edge. The resulting static image on the secondary display is functionally useless and aesthetically undesirable.

**The Solution:** Contextual State Transition.

**Action:**
1. Monitor the cursor's absolute X/Y coordinates.
2. When the cursor physically crosses the boundary onto the secondary display, intercept this event.
3. Instead of showing the clamped screen capture, fade or slide in a sleek, semi-transparent PyQt5 overlay ("The HUD") over the Surgical Zoom window.
4. This HUD will house minimalist, custom-styled dropdowns and sliders for: **Key Mapping**, **Default Zoom Ratio**, and **Toggle/Momentary preferences**.
5. The moment the cursor crosses back to the Primary Monitor bounds, dissolve the HUD and instantly resume the live `mss` capture feed.

**Files:** `gui/zoom_window.py`, `core/capture_engine.py` (cursor-in-monitor check), potentially new `gui/hud_overlay.py`

---

## Status Legend
- ⬜ Pending
- 🔄 In Progress
- ✅ Implemented

---

## Changelog
*(Add entries when items are implemented.)*

- **Phase 1** (branch: `feature/phase-1-visual-stability`):
  - **1.1** Aspect ratio lock: `compute_capture_dimensions()` in `core_capture.py` derives capture width/height from target display; `compute_centered_region` and `CaptureEngine.capture_cursor_region` accept rectangular dimensions; ZoomWindow scales pixmap to fill target resolution.
  - **1.2** Synthetic cursor: `CursorOverlay` widget in `gui/zoom_window.py` draws a minimalist white crosshair with dark outline, centered on canvas; `WA_TransparentForMouseEvents` so it does not block input.
