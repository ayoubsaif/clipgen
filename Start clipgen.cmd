@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import clipgen" >nul 2>nul
if not errorlevel 1 goto start
echo Setting up clipgen. This can take a few minutes.
call "scripts\setup.cmd"
if not errorlevel 1 goto start
echo Setup failed.
pause
exit /b 1

:start
start "" ".venv\Scripts\pythonw.exe" -m clipgen.cli ui --window
