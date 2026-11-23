@echo off
setlocal
cd /d "%~dp0.."
where uv >nul 2>nul
if errorlevel 1 goto no_uv

set "UV_ARGS="
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" goto arm
if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" goto arm
where nvidia-smi >nul 2>nul
if not errorlevel 1 set "UV_ARGS=--extra gpu"
goto sync

:arm
rem Whisper has no Windows ARM build, so clipgen runs on x64 Python through Windows emulation.
echo Windows on ARM detected. Installing the x64 version, with Whisper on the CPU.
set "UV_ARGS=--python cpython-3.12-windows-x86_64-none"

:sync
uv sync %UV_ARGS%
exit /b %errorlevel%

:no_uv
echo uv is not installed. Install it from https://docs.astral.sh/uv/ and try again.
exit /b 1
