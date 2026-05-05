@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Content-Factory Length Filter

set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"
%PY_CMD% --version >nul 2>nul || goto :NO_PYTHON

set "MODE=%~1"
set "STORIES_DIR=%~2"
set "SHORT_DIR=%~3"

if "%MODE%"=="" goto :MENU
goto :RUN_FROM_ARGS

:MENU
echo.
echo ============================================
echo  Content-Factory: Первичная фильтрация
echo ============================================
echo.
echo Режимы:
echo   1 ^) DRY-RUN   - ничего не перемещает, только отчет/план
echo   2 ^) EXECUTE   - перемещает только рассказы ^< 15 минут
echo.
set /p MODE_CHOICE=Выбери режим [1/2]: 
if "%MODE_CHOICE%"=="1" set "MODE=dry-run"
if "%MODE_CHOICE%"=="2" set "MODE=execute"
if "%MODE%"=="" (
    echo [ERROR] Неверный режим.
    echo.
    goto :USAGE
)
set /p STORIES_DIR=Путь к папке с рассказами: 
set /p SHORT_DIR=Путь к short_under_15m (Enter = по умолчанию): 
goto :RUN

:RUN_FROM_ARGS
if /i "%MODE%"=="dry-run" goto :RUN
if /i "%MODE%"=="execute" goto :RUN
echo [ERROR] Неизвестный режим: %MODE%
echo.
goto :USAGE

:RUN
if "%STORIES_DIR%"=="" (
    echo [ERROR] Нужно указать папку рассказов.
    echo.
    goto :USAGE
)

echo.
echo [INFO] MODE        = %MODE%
echo [INFO] STORIES_DIR = %STORIES_DIR%
if not "%SHORT_DIR%"=="" echo [INFO] SHORT_DIR   = %SHORT_DIR%
echo [INFO] Формула: estimated_minutes = word_count / 150
echo [INFO] Порог: меньше 15 минут -> short_under_15m
echo.

set "CMD=%PY_CMD% -m orchestrator filter-length --stories-dir "%STORIES_DIR%""
if not "%SHORT_DIR%"=="" set "CMD=%CMD% --short-dir "%SHORT_DIR%""
if /i "%MODE%"=="execute" set "CMD=%CMD% --execute"

echo [INFO] Запуск: %CMD%
echo.
call %CMD%
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo [DONE] Фильтрация завершена успешно.
    echo [INFO] Отчет: .orchestrator\reports\filter_report.csv
) else (
    echo [ERROR] Завершено с ошибкой. Код: %EXIT_CODE%
)
echo.
pause
exit /b %EXIT_CODE%

:USAGE
echo Использование:
echo   %~nx0 dry-run  "D:\path\to\stories" ["D:\path\to\short_under_15m"]
echo   %~nx0 execute  "D:\path\to\stories" ["D:\path\to\short_under_15m"]
echo.
echo Где:
echo   dry-run  - только отчет и план действий, без перемещений
echo   execute  - перемещает только рассказы ^< 15 минут
echo.
pause
exit /b 1

:NO_PYTHON
echo [ERROR] Python не найден в PATH.
echo [ERROR] Установи Python 3.11+ и перезапусти .bat
echo.
pause
exit /b 1
