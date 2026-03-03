@echo off
cd /d "%~dp0"

echo.
echo Enter folder path (main folder with categories and stories).
echo You can drag-and-drop the folder here.
echo.
set /p FOLDER="Path: "

if "%FOLDER%"=="" (
    echo.
    echo No path. Using all_stories next to script...
    python -u "download_first_parts.py"
) else (
    set "FOLDER=%FOLDER:"=%"
    echo.
    python -u "download_first_parts.py" "%FOLDER%"
)

echo.
pause
