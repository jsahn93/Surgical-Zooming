# Surgical Zooming

Surgical Zooming is a small PyQt5-based remote desktop zoom tool. It opens a
frameless, always-on-top window that continuously captures a region around the
mouse cursor (from the primary monitor only) using `mss` and displays a
zoomed-in view. Global input hooks are provided via `pynput` and a low-level scroll hook.

## Features

- Zooms a configurable square region (`ZOOM_SIZE`) around the mouse cursor.
- Renders into a frameless, always-on-top PyQt5 window.
- Uses a configurable zoom factor (`ZOOM_FACTOR`).
- Avoids "screen inception" by capturing only from the primary monitor.
- Global hotkeys:
  - `Esc`: Quit the application.
  - `Ctrl+Space`: Toggle zoom window visibility.

## Environment Setup

1. Create and activate a virtual environment in `.venv`:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # on Windows PowerShell/cmd
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Running the Tool

The main entry point is `main.py`, which exposes its CLI via `python-fire`.

From an activated `.venv`:

```bash
python main.py
```

This will launch the zoom window with the default configuration.

You can also call `main` explicitly via Fire:

```bash
python main.py main
```

Alternatively, you can launch via the provided batch file (recommended on
Windows for non-technical users):

```bat
run.bat
```

## Build Pipeline (PyInstaller + GitHub Actions)

- This repository includes a GitHub Actions workflow at
  `.github/workflows/pyinstaller.yml` that:
  - Creates a fresh virtual environment.
  - Installs `requirements.txt` (including `pyinstaller`).
  - Builds a one-file, windowed executable using PyInstaller.
  - Uploads the built `.exe` as an artifact and attaches it to a GitHub
    Release when a new tag (`v*`) is pushed.

