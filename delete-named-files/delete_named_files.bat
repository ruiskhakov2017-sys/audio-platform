@echo off
setlocal
cd /d "%~dp0"

echo Enter folder path to process (or press Enter to use current folder):
echo You can paste path or drag-and-drop folder here.
set /p "FOLDER="
if "%FOLDER%"=="" set "FOLDER=%~dp0"
set "FOLDER=%FOLDER:"=%"

if not exist "%FOLDER%" (
    echo Folder not found: %FOLDER%
    pause
    exit /b 1
)

echo.
echo Deleting in: %FOLDER%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0delete_named_files.ps1" -RootFolder "%FOLDER%"

echo.
pause
