@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Dokachka

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
echo === Dokachka pervoj glavy ===
echo.
echo === SAFE MODE: bez vibora papki net zapuska ===
echo.

set "FOLDER="
for /f "usebackq delims=" %%I in (`
    powershell -NoProfile -ExecutionPolicy Bypass -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description='Vyberi papku s rasskazami'; $f.RootFolder=[System.Environment+SpecialFolder]::MyComputer; $f.SelectedPath='D:\'; if($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$f.SelectedPath}"
`) do set "FOLDER=%%I"

if "%FOLDER%"=="" (
    echo Otmeneno polzovatelem.
    echo.
    pause
    exit /b 1
)

echo Vybrana papka: "%FOLDER%"
echo.
echo Zapusk download_first_parts.py...
python -u "download_first_parts.py" "%FOLDER%"
echo.
echo Exit code: %errorlevel%
echo.
pause
