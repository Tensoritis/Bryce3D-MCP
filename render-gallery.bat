@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
set "BRYCE3D_OUTPUT_DIR=%~dp0workspace"
echo This builds the five example scenes and renders each one in your running Bryce.
echo Start Bryce 3D first, SAVE YOUR WORK, and leave Bryce un-minimized. It can stay
echo behind other windows. Each scene replaces whatever Bryce has open.
pause
".venv\Scripts\python.exe" examples\gallery.py build
if errorlevel 1 goto end
".venv\Scripts\python.exe" examples\gallery.py render
:end
pause
