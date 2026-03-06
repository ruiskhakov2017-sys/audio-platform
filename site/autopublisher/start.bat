@echo off
cd /d "%~dp0"
title Autopublisher [%CD%]
set LOG=autopublisher_run.log

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run: python -m venv .venv
    pause
    exit /b 1
)

echo === %date% %time% === > "%LOG%"
echo === Папка: %CD% >> "%LOG%"
echo. >> "%LOG%"

".venv\Scripts\python.exe" "publish_stories.py" >> "%LOG%" 2>&1
set EXIT_CODE=%errorlevel%
echo. >> "%LOG%"
echo === Код выхода: %EXIT_CODE% >> "%LOG%"

echo.
echo ========== Лог запуска (см. также файл %LOG%) ==========
type "%LOG%"
echo.
echo ========== Конец лога. Файл: %CD%\%LOG% ==========
pause
