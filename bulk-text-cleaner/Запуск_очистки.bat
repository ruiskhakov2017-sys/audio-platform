@echo off
cd /d "%~dp0"

echo Starting...
where python >nul 2>&1
if errorlevel 1 (
    py -3 "clean_stories_gui.py" 2>&1
) else (
    python "clean_stories_gui.py" 2>&1
)
if errorlevel 1 (
    echo.
    echo Error. Check error_log.txt in this folder.
    echo Install Python and run: pip install tqdm
)
echo.
pause
