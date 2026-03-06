@echo off
setlocal enabledelayedexpansion
if "%~1"=="" (cmd /k "%~f0" _ & exit /b)
chcp 65001 >nul
title Restore files

echo.
echo  ========================================
echo   RESTORE FILES - replace corrupted
echo  ========================================
echo.
echo  Step 1. Folder with CORRUPTED files
echo  Files here will be overwritten by originals.
echo.
set /p "TARGET_DIR=  Enter path: "
echo.
echo  Step 2. Folder with ORIGINALS
echo  Good files will be copied from here.
echo.
set /p "SOURCE_DIR=  Enter path: "
echo.

set "TARGET_DIR=%TARGET_DIR:"=%"
set "SOURCE_DIR=%SOURCE_DIR:"=%"

if not exist "%TARGET_DIR%" (echo ERROR: Folder not found: "%TARGET_DIR%" & goto :end)
if not exist "%SOURCE_DIR%" (echo ERROR: Folder not found: "%SOURCE_DIR%" & goto :end)

echo  TARGET ^(overwrite^): %TARGET_DIR%
echo  SOURCE ^(from^): %SOURCE_DIR%
echo.
echo  Starting...
echo.

set "TARGET_DIR=%TARGET_DIR%"
set "SOURCE_DIR=%SOURCE_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore.ps1"

echo.
echo Done.
:end
pause
