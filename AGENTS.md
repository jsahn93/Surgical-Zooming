# Surgical Zooming – Agent Bridge

## Overview & Architecture
- **Purpose**: Local-only surgical zoom viewer for remote desktop / screen work.
- **Architecture**:
  - Core module `core_capture.py`:
    - Computes cursor-centered capture regions clamped to the primary monitor.
    - Capture dimensions match target display aspect ratio (anti-pillarboxing).
    - Performs screen capture with `mss`.
    - Computes zoom window dimensions from configuration values.
  - GUI module `main.py`:
    - Uses PyQt5 to render the zoomed image in a frameless, always-on-top window with a semi-transparent close `✕` button.
    - `gui/zoom_window.py` overlays a synthetic crosshair at the canvas center (mss bypasses hardware cursor).
    - Calls into `core_capture` for all capture and zoom math.
    - **Phase 2:** Combo toggle (default `Ctrl+Caps Lock`, rebindable via HUD with high-water combo capture), Glass Desktop yield (hide when inactive), Ctrl+Scroll zoom (only when active + cursor on primary). Input via `pynput` keyboard/mouse listeners; no OS toggle-key enforcement.
- **Separation of concerns**: Capture and zoom logic live in `core_capture.py`; `main.py` is purely responsible for UI wiring and display.

## Dependencies (requirements.txt)
- `pyqt5`: GUI framework for the zoom window.
- `mss`: Screen capture around the mouse cursor, primary monitor only.
- `pynput`: Global keyboard and mouse listeners for `Esc` (quit) and combo toggle (default `Ctrl+Caps Lock`; rebindable via HUD). Ctrl+Scroll zoom uses a low-level hook in `input_hooks.py`.
- `fire`: Exposes the `main()` entry point as a CLI via `python-fire`.
- `pyinstaller`: Used by CI to build a one-file, windowed `.exe`.

## CLI / Execution Model
- **Entry script**: `main.py`
  - `if __name__ == "__main__": import fire; fire.Fire(main)`
  - Primary command: `main` (no arguments; future options can be added as parameters).
- **Batch launcher**: `run.bat`
  - Activates local `.venv` if present.
  - Runs `python main.py` so non-technical users can double-click to start.

## Environment & Distribution
- **Local only**: No remote connections; all capture and logic are strictly on the local PC.
- **Virtual env**: Always use `.venv` at the repo root; dependencies managed via `requirements.txt`.
- **Packaging target**: PyInstaller-based build to a single-click `.exe`. Build via `pyinstaller SurgicalZooming.spec` (one-file, windowed). Paths for any bundled assets (e.g. `settings.json`) must use `main.resolve_resource_path()` so they resolve to `sys._MEIPASS` when frozen.
- **CI pipeline**: GitHub Actions workflow (`.github/workflows/pyinstaller.yml`) runs on tag push `v*` (e.g. `v1.0.0`): checks out repo, sets up Python 3.11 on `windows-latest`, installs deps, runs PyInstaller, uploads the `.exe` as an artifact and attaches it to the corresponding GitHub Release.

## Immediate Pending Tasks
- Add basic automated tests around `core_capture.py` (capture region math, window size calculation).
- Extend CLI options (via `python-fire`) for user-tunable parameters like `ZOOM_SIZE`, `ZOOM_FACTOR`, and update docs accordingly.

