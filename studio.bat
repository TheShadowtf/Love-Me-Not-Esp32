@echo off
setlocal
set "SCRIPT=%~dp0tools\oled_studio.py"
python "%SCRIPT%"
if errorlevel 1 (
    echo.
    echo Failed to launch OLED Studio.
    pause
)
