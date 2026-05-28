@echo off
cd /d "%~dp0"

set "SCRIPT=clean_stories_gui.py"

if not exist "%SCRIPT%" (
    echo [ERROR] File not found: %SCRIPT%
    echo Put this .bat near the cleaner scripts.
    echo.
    pause
    exit /b 1
)

echo Starting cleaner...

where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%SCRIPT%"
    if not errorlevel 1 exit /b 0
)

where python >nul 2>&1
if not errorlevel 1 (
    python "%SCRIPT%"
    if not errorlevel 1 exit /b 0
)

echo.
echo [ERROR] Failed to start cleaner.
echo If Python is not installed, install Python 3.10+.
echo If modules are missing, run: pip install -r requirements.txt
echo Check error_log.txt in this folder for details.
echo.
pause
exit /b 1
