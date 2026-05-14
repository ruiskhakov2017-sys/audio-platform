@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Проверка состояния R2

echo.
echo ====================================================
echo   ПРОВЕРКА R2 — только чтение, ничего не удаляет
echo ====================================================
echo.

".venv\Scripts\python.exe" check_r2_status.py

echo.
pause
