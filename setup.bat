@echo off
setlocal
cd /d "%~dp0"
echo Installing Bryce3D-MCP...
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Python 3.10 or newer is required: https://www.python.org/downloads/
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --quiet -e .
if errorlevel 1 (
  echo Installation failed.
  pause
  exit /b 1
)
echo.
echo Checking the connection to Bryce. Start Bryce 3D first and leave the mouse alone.
timeout /t 5
set "BRYCE3D_OUTPUT_DIR=%~dp0workspace"
".venv\Scripts\python.exe" -m bryce3d_mcp --check
echo.
echo To add it to Claude Code, run:
echo   claude mcp add bryce3d -e "BRYCE3D_OUTPUT_DIR=%~dp0workspace" -- "%~dp0.venv\Scripts\python.exe" -m bryce3d_mcp
echo For Claude Desktop, see README.md.
pause
