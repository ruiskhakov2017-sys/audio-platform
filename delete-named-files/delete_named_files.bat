@echo off
if "%~1"=="" (cmd /k "%~f0" _ & exit /b)
setlocal
cd /d "%~dp0"

echo Enter folder path to process (or press Enter to use current folder):
echo You can paste path or drag-and-drop folder here.
set /p "FOLDER="
set "FOLDER=%FOLDER:"=%"
if "%FOLDER%"=="" set "FOLDER=%~dp0"

echo.
echo Deleting in: %FOLDER%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0delete_named_files.ps1" -RootFolder "%FOLDER%"

echo.
:end
pause
