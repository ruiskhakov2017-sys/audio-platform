@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Autopublisher [%CD%]
set "LOG=%~dp0autopublisher_run.log"
set "TMPLOG=%~dp0autopublisher_run.tmp"

echo.
echo Autopublisher. Folder: %CD%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Creating .venv and installing dependencies...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv. Install Python and run: python -m venv .venv
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install requirements.
        pause
        exit /b 1
    )
    echo Done. Run start.bat again.
    pause
    exit /b 0
)

echo Installing/updating dependencies...
".venv\Scripts\pip.exe" install -r requirements.txt -q
if errorlevel 1 (
    echo Pip install failed. Check requirements.txt
    pause
    exit /b 1
)

echo Starting program. If a window with PUSH button appears - click it.
echo After finish, log will show here and open in Notepad.
echo.
pause

del /q "%TMPLOG%" 2>nul
echo === %date% %time% === > "%TMPLOG%"
echo === Folder: %CD% >> "%TMPLOG%"
echo. >> "%TMPLOG%"

".venv\Scripts\python.exe" "publish_stories.py" >> "%TMPLOG%" 2>&1
set EXIT_CODE=%errorlevel%
echo. >> "%TMPLOG%"
echo === Exit code: %EXIT_CODE% >> "%TMPLOG%"

copy /y "%TMPLOG%" "%LOG%" >nul
del /q "%TMPLOG%" 2>nul

echo.
echo ========== Log (full file in Notepad) ==========
type "%LOG%"
echo.
echo Opening log in Notepad...
start notepad "%LOG%"
pause
