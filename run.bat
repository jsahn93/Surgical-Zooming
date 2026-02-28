@echo off
setlocal

REM Activate the local virtual environment, if it exists
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

REM Run the Surgical Zooming tool via python-fire
python main.py %*

endlocal
