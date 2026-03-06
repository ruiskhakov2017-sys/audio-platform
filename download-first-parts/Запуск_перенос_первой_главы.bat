@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo === First chapter to top (perenos pervoj glavy) ===
echo.
set /p FOLDER="Folder: "
if "%FOLDER%"=="" (
    echo No folder.
    goto end
)
set "FOLDER=%FOLDER:"=%"
echo.
python -u "move_first_chapter_to_top.py" "%FOLDER%"

:end
echo.
pause
