@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up clipgen for the first time. This can take a few minutes.
    uv sync --extra gpu
    if errorlevel 1 (
        echo Setup failed. Check that uv is installed.
        pause
        exit /b 1
    )
)
start "" ".venv\Scripts\pythonw.exe" -m clipgen.cli ui --window
