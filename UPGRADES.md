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

## Phase 2: Global Input & State Machine (Functional Upgrades)

**Objective:** Replace the simple hotkey toggle with a dual-mode state machine. Maintain clinical/industrial aesthetic. No new dependencies.

### 2.1 Capslock Logic (The Primary Switch)
**Status:** ✅ Implemented

**Monitor:** Listen for both `press` and `release` events on `Capslock` (using `keyboard` library).

**Timer:** On `press`, record `start_time`.

**Logic:**
- **Release < 300ms:** Register as **Toggle**. Flip the `IS_ACTIVE` state (On → Off or Off → On).
- **Release ≥ 300ms:** Register as **Momentary**. Set `IS_ACTIVE = False` immediately upon release. (Momentary: activates on press, deactivates on release.)

**Files:** `main.py` (or hotkey/state module)

---

### 2.2 The "Glass Desktop" Yield
**Status:** ✅ Implemented

**Logic:** When Surgical Zoom is inactive, it must not hog the secondary display with a blank or frozen canvas.

**Action:**
- When `IS_ACTIVE == False`: The PyQt5 window calls `.hide()` to reveal underlying OS/Apps on the secondary monitor.
- When `IS_ACTIVE == True`: The window calls `.showFullScreen()`.

**Files:** `main.py`, `gui/zoom_window.py`

---

### 2.3 Dynamic Zoom (Ctrl + Scroll)
**Status:** ✅ Implemented

**Logic:** Implement a listener for `Ctrl + MouseWheel` to modify `ZOOM_FACTOR`.

**Constraint:** The listener must *only* modify zoom if:
1. `IS_ACTIVE == True`
2. The mouse X, Y are within the **Primary Monitor** bounds

**Files:** `main.py`, `gui/zoom_window.py` (zoom_factor state)

---

## Phase 3: Proximity HUD & Coordinate Geofencing (GUI Evolution)

**Objective:** Secondary display acts as a "Contextual Control Panel." No new dependencies.

### 3.1 The Boundary Trigger
**Status:** ⬜ Pending

**Logic:** Continuously monitor absolute cursor position (X, Y).

**Condition:** If `X > Primary_Monitor_Right_Edge` (i.e. `X > Primary_Monitor_Left + Primary_Monitor_Width`), the cursor is on the Secondary Display.

**Files:** `gui/zoom_window.py`, `core/capture_engine.py`

---

### 3.2 State Transition & HUD Mode
**Status:** ⬜ Pending

**Standard Operation:** While cursor is on Primary, display the live `mss` capture.

**HUD Mode:** While cursor is on Secondary, stop updating the live capture. Display a semi-transparent, dark-themed **Settings HUD** (PyQt5 overlay) instead.

**Files:** `gui/zoom_window.py`

---

### 3.3 HUD Content (Minimalist/Industrial)
**Status:** ⬜ Pending

**Controls:**
1. **Zoom Multiplier Slider** – Visual control for zoom level.
2. **Key Mapping Dropdown** – Allow user to rebind the toggle key.
3. **Pen Mode Toggle (Boolean)** – If OFF: normal HUD behavior. If ON: HUD remains invisible even on secondary (prep for future S-Pen coordinate warping).

**Files:** `gui/hud_overlay.py` or `gui/zoom_window.py`

---

### 3.4 Instant Reversion
**Status:** ⬜ Pending

**Logic:** As soon as `X < Primary_Monitor_Right_Edge` (cursor back on Primary), the HUD must vanish and the live zoom feed must resume instantly.

**Files:** `gui/zoom_window.py`

---

---

## Forensic Constraints (All Phases)

- **No AHK:** All logic must be Python-native (`keyboard`, `PyQt5`).
- **Coordinate Clamping:** Do **not** remove the logic that prevents "Zoomception." The capture center must stay clamped to the primary monitor edge even when the mouse moves to the secondary display for HUD interaction.
- **Performance:** HUD rendering must not create a background CPU spike. Use `Qt.WA_TranslucentBackground` for the HUD aesthetic.

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
- **Phase 2** (re-implemented for Python 3.11 / 64-bit):
  - **2.1** Capslock dual-mode: `keyboard.on_press_key` / `on_release_key`; <300ms = toggle, ≥300ms = momentary.
  - **2.2** Glass Desktop: window starts hidden; `hide()` / `showFullScreen()` from `IS_ACTIVE`; `update_frame` skips when hidden.
  - **2.3** Ctrl+Scroll: `input_hooks.py` with 64-bit-safe ctypes (SetWindowsHookExW, GetModuleHandleW restype/argtypes); zoom only when active and cursor on primary; zoom_factor 0.5–4.0.
  - **core_capture**: `is_cursor_on_primary()` for primary bounds.
