@echo off
cd /d "%~dp0"
title Откат имён файлов

echo.
echo Запуск отката имён...
echo Сейчас откроется окно выбора папки.
echo.

python "revert_names_gui.py" 2>&1
if errorlevel 1 (
    echo Пробуем py...
    py -3 "revert_names_gui.py" 2>&1
)

echo.
echo Нажми любую клавишу, чтобы закрыть окно...
pause >nul
