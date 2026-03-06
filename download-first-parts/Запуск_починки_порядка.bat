@echo off
cd /d "%~dp0"

echo.
echo === Fix chapter order ===
echo.
echo Enter folder path or drag folder here.
echo.
set /p FOLDER="Folder: "

if "%FOLDER%"=="" (
    echo No folder. Run again and enter path.
    goto end
)

set "FOLDER=%FOLDER:"=%"
echo.
python -u "fix_first_chapter_order.py" "%FOLDER%"

:end
echo.
pause
