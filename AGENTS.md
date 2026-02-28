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
    - Uses global hotkeys (`keyboard`) to toggle visibility and exit.
- **Separation of concerns**: Capture and zoom logic live in `core_capture.py`; `main.py` is purely responsible for UI wiring and display.

## Dependencies (requirements.txt)
- `pyqt5`: GUI framework for the zoom window.
- `mss`: Screen capture around the mouse cursor, primary monitor only.
- `keyboard`: Global hotkeys (`Esc`, `Ctrl+Space`) to quit / toggle.
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
- **Packaging target**: PyInstaller-based build to a single-click `.exe`. Paths for any future assets must be compatible with `sys._MEIPASS`.
- **CI pipeline**: GitHub Actions workflow (`.github/workflows/pyinstaller.yml`) builds the Windows `.exe` on tagged pushes (`v*`), uploads it as an artifact, and attaches it to a GitHub Release.

## Immediate Pending Tasks
- Add basic automated tests around `core_capture.py` (capture region math, window size calculation).
- Extend CLI options (via `python-fire`) for user-tunable parameters like `ZOOM_SIZE`, `ZOOM_FACTOR`, and update docs accordingly.

