@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Dokačka

if not exist "download_first_parts.py" (
    echo OSIBKA: V papke net faila download_first_parts.py
    echo Papka batnika: %~dp0
    pause
    exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
    echo OSIBKA: Python ne najden. Postav Python ili dobav v PATH.
    pause
    exit /b 1
)

echo.
echo === Dokačka pervoj glavy ===
echo.
set /p FOLDER="Folder path (or Enter for all_stories): "

set "FOLDER=%FOLDER:"=%"
if "%FOLDER%"=="" (
    python -u "download_first_parts.py"
) else (
    python -u "download_first_parts.py" "%FOLDER%"
)
echo.
pause
