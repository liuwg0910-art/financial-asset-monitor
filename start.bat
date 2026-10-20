@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 (
    python scripts\start.py
) else (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Please install Python 3.11-3.13 and enable Add Python to PATH.
        pause
        exit /b 1
    )
    py -3 scripts\start.py
)
if errorlevel 1 (
    echo Startup failed. See the error above.
    pause
    exit /b 1
)
