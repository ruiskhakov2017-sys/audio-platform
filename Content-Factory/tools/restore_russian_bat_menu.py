# -*- coding: utf-8 -*-
"""Restore Russian main menu + [Q] queue submenu in Content-Factory-Запуск.bat (Windows-1251)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bat = ROOT / "Content-Factory-Запуск.bat"
raw = bat.read_text(encoding="cp1251")
# Якорь — реальный запуск сайта (после блока stories\input), не префикс :RUN_SITE_PIPELINE_KOKORO_DRIVE.
marker = "\n:RUN_SITE_PIPELINE\ncls\n"
idx = raw.find(marker)
if idx == -1:
    raise SystemExit("marker :RUN_SITE_PIPELINE\\ncls\\n not found")

lines = raw.split("\n")
head = "\n".join(lines[:13]) + "\n"

MIDDLE = r""":MAIN_MENU
cls
echo ============================================================
echo   Контент-завод — центр запуска
echo ============================================================
echo.
echo [1] Сайт: обработать часть рассказов
echo [2] Сайт: обработать все рассказы
echo [3] Сайт: продолжить после остановки
echo [4] Сайт: отдельные этапы
echo [5] Сайт: озвучка и MP3-инструменты
echo [6] YouTube: подготовить видео полностью
echo [7] YouTube: отдельные этапы
echo [8] Проверки, статусы и логи
echo [9] Настройки и сервисные команды
echo [Q] Пополнить очередь stories\input
echo [0] Выход
echo.
set /p MAIN_CHOICE=Выберите пункт [0-9 Q]: 
set "MAIN_CHOICE=%MAIN_CHOICE: =%"
if /I "%MAIN_CHOICE%"=="Q" goto :STORIES_INPUT_MENU
set "MAIN_CHOICE=%MAIN_CHOICE:~0,1%"
if "%MAIN_CHOICE%"=="1" goto :RUN_SITE_PARTIAL
if "%MAIN_CHOICE%"=="2" goto :RUN_SITE_FULL
if "%MAIN_CHOICE%"=="3" goto :RESUME_SITE_PIPELINE
if "%MAIN_CHOICE%"=="4" goto :STAGE_MENU
if "%MAIN_CHOICE%"=="5" goto :SITE_TTS_TEST_MENU
if "%MAIN_CHOICE%"=="6" goto :RUN_YOUTUBE_PIPELINE
if "%MAIN_CHOICE%"=="7" goto :YT_STAGES_MENU
if "%MAIN_CHOICE%"=="8" goto :CHECKS_MENU
if "%MAIN_CHOICE%"=="9" goto :SERVICE_MENU
if "%MAIN_CHOICE%"=="0" goto :EOF_EXIT
echo [WARN] Неизвестный пункт: "%MAIN_CHOICE%"
timeout /t 1 /nobreak >nul
goto :MAIN_MENU

:RUN_SITE_PARTIAL
cls
echo ============================================================
echo   Сайт: обработать часть рассказов
echo ============================================================
echo Текущий лимит Phase A: %PHASE_A_LIMIT_LABEL%
if /I "%PHASE_A_LIMIT_MODE%"=="FULL" (
  echo [WARN] Сейчас включён режим FULL. Для частичной обработки нужен TEST.
  choice /c YN /n /m "Переключить лимит на TEST перед запуском? [Y/N]: "
  if errorlevel 2 goto :MAIN_MENU
  > "%PHASE_A_LIMIT_FILE%" echo TEST
  call :INIT_PHASE_A_LIMIT_MODE
)
echo.
echo Будет запущена полная цепочка сайта с текущим лимитом Phase A.
choice /c YN /n /m "Продолжить? [Y/N]: "
if errorlevel 2 goto :MAIN_MENU
goto :RUN_SITE_PIPELINE

:RUN_SITE_FULL
cls
echo ============================================================
echo   Сайт: обработать все рассказы
echo ============================================================
if /I not "%PHASE_A_LIMIT_MODE%"=="FULL" (
  echo [INFO] Для полного прогона рекомендуется лимит Phase A: FULL.
  choice /c YN /n /m "Переключить лимит Phase A на FULL? [Y/N]: "
  if errorlevel 2 goto :MAIN_MENU
  > "%PHASE_A_LIMIT_FILE%" echo FULL
  call :INIT_PHASE_A_LIMIT_MODE
)
echo.
echo [1] Обычный полный прогон сайта ^(Phase A + Phase B + run site^)
echo [2] Полный прогон + Kokoro Google Drive TTS
echo [0] Назад в главное меню
echo.
set /p FULL_CHOICE=Выберите: 
if "%FULL_CHOICE%"=="0" goto :MAIN_MENU
if "%FULL_CHOICE%"=="1" goto :RUN_SITE_PIPELINE
if "%FULL_CHOICE%"=="2" goto :RUN_SITE_PIPELINE_KOKORO_DRIVE
goto :RUN_SITE_FULL

:CHECKS_MENU
cls
echo ============================================================
echo   Проверки, статусы и логи
echo ============================================================
echo.
echo [1] Preflight ^(среда, сухой прогон^)
echo [2] Открыть папку отчётов ^(.orchestrator\reports^)
echo [3] Открыть логи и status/events
echo [4] Показать текущие режимы runtime
echo [0] Назад
echo.
set /p CHK=Выберите пункт: 
if "%CHK%"=="0" goto :MAIN_MENU
if "%CHK%"=="1" goto :PREFLIGHT
if "%CHK%"=="2" goto :OPEN_REPORTS
if "%CHK%"=="3" goto :OPEN_LOGS
if "%CHK%"=="4" goto :SHOW_MODES
goto :CHECKS_MENU

:SERVICE_MENU
cls
echo ============================================================
echo   Настройки и сервисные команды
echo ============================================================
echo.
echo [1] Режимы runtime ^(визуал, TTS, публикация, видео^)
echo [2] Переключить лимит Phase A ^(TEST / FULL^)
echo [3] Cleanup / карантин
echo [4] Phase B scaffold ^(не production^)
echo [5] Полный сайт + Kokoro Google Drive TTS
echo [0] Назад
echo.
set /p SVC=Выберите пункт: 
if "%SVC%"=="0" goto :MAIN_MENU
if "%SVC%"=="1" goto :MODES_MENU
if "%SVC%"=="2" goto :TOGGLE_PHASE_A_LIMIT_MODE
if "%SVC%"=="3" goto :CLEANUP_MENU
if "%SVC%"=="4" goto :RUN_SCAFFOLD_PHASE_B
if "%SVC%"=="5" goto :RUN_SITE_PIPELINE_KOKORO_DRIVE
goto :SERVICE_MENU

:YT_STAGES_MENU
cls
echo ============================================================
echo   YouTube: отдельные этапы
echo ============================================================
echo.
echo Отдельные шаги YouTube здесь не вынесены: используйте полный пайплайн ^(пункт [6]^)
echo или команды из документации проекта.
echo.
pause
goto :MAIN_MENU

:STORIES_INPUT_MENU
cls
echo ============================================================
echo   Очередь: пополнение stories\input
echo ============================================================
echo Команда: orchestrator sample-library ^(MOVE; имя уже в очереди — пропуск^).
echo Файлы переносятся в stories\input и исчезают из исходных категорий библиотеки.
echo Подпапка _series не обрабатывается.
echo.
echo [1] Проверить пополнение без выполнения
echo [2] Пополнить очередь по 50 рассказов из каждой папки
echo [3] Пополнить очередь с другим лимитом
echo [4] Открыть stories\input
echo [0] Назад
echo.
set /p SI_CHOICE=Выберите пункт: 
if "%SI_CHOICE%"=="0" goto :MAIN_MENU
if "%SI_CHOICE%"=="4" (
  start "" "%~dp0stories\input"
  goto :STORIES_INPUT_MENU
)
if "%SI_CHOICE%"=="1" goto :STORIES_INPUT_RUN_DRY
if "%SI_CHOICE%"=="2" goto :STORIES_INPUT_RUN50
if "%SI_CHOICE%"=="3" goto :STORIES_INPUT_RUN_CUSTOM
goto :STORIES_INPUT_MENU

:STORIES_INPUT_ASK_LIB
cls
echo ============================================================
echo   Каталог библиотеки ^(корень, внутри — папки-категории^)
echo ============================================================
if not "%LIBRARY_SOURCE_DIR%"=="" echo Переменная LIBRARY_SOURCE_DIR=%LIBRARY_SOURCE_DIR%
echo.
set "LIB_SRC="
set /p LIB_SRC=Путь к корню библиотеки ^(Enter = значение LIBRARY_SOURCE_DIR^): 
if "%LIB_SRC%"=="" set "LIB_SRC=%LIBRARY_SOURCE_DIR%"
if "%LIB_SRC%"=="" (
  echo [ERROR] Не задан путь. Укажите каталог или задайте LIBRARY_SOURCE_DIR.
  pause
  goto :STORIES_INPUT_MENU
)
if not exist "%LIB_SRC%" (
  echo [ERROR] Путь не найден: %LIB_SRC%
  pause
  goto :STORIES_INPUT_MENU
)
goto :eof

:STORIES_INPUT_RUN50
call :STORIES_INPUT_ASK_LIB
set "PER_N=50"
goto :STORIES_INPUT_WARN_RUN

:STORIES_INPUT_RUN_CUSTOM
call :STORIES_INPUT_ASK_LIB
set "PER_N="
set /p PER_N=Сколько новых рассказов из каждой верхней папки ^(N^): 
if "%PER_N%"=="" goto :STORIES_INPUT_MENU
goto :STORIES_INPUT_WARN_RUN

:STORIES_INPUT_RUN_DRY
call :STORIES_INPUT_ASK_LIB
set "PER_N="
set /p PER_N=N для проверки ^(Enter = 50^): 
if "%PER_N%"=="" set "PER_N=50"
cls
echo [DRY-RUN] orchestrator sample-library — только план
echo.
%PY_CMD% -m orchestrator sample-library --source-dir "%LIB_SRC%" --target-dir "%~dp0stories\input" --per-folder %PER_N% --dry-run
echo.
echo Сводка выше ^(skipped_queue_basename, per_folder_planned, manifest_json^).
pause
goto :STORIES_INPUT_MENU

:STORIES_INPUT_WARN_RUN
cls
echo [ВНИМАНИЕ] Будут перенесены ^(MOVE^) .txt во входную очередь stories\input.
echo Исходные файлы для выбранных имён исчезнут из категорий библиотеки.
echo Имена, уже есть в корне stories\input, не берутся повторно.
echo N=%PER_N%, источник: %LIB_SRC%
echo.
choice /c YN /n /m "Продолжить? [Y/N]: "
if errorlevel 2 goto :STORIES_INPUT_MENU
cls
echo [RUN] orchestrator sample-library — выполнение переноса
echo.
%PY_CMD% -m orchestrator sample-library --source-dir "%LIB_SRC%" --target-dir "%~dp0stories\input" --per-folder %PER_N% --allow-nonempty-target --execute --confirm-move
if errorlevel 1 echo [ERROR] sample-library завершился с ошибкой.
echo.
echo Сводка и пути отчётов — в выводе выше.
pause
goto :STORIES_INPUT_MENU

"""

new_raw = head + MIDDLE + "\n" + raw[idx + 1 :]
# title line in head - replace line 5
new_lines = new_raw.split("\n")
for i, ln in enumerate(new_lines):
    if ln.startswith("title "):
        new_lines[i] = "title Контент-завод — меню запуска"
        break
new_raw = "\n".join(new_lines)

# EOF_EXIT Russian
new_raw = new_raw.replace("echo Exiting.\r", "echo Выход.")
new_raw = new_raw.replace("echo Exiting.", "echo Выход.")

# cmd.exe надёжно парсит call :label только с CRLF; LF-only даёт «метка не найдена».
bat.write_text(new_raw, encoding="cp1251", newline="\r\n")
print("written", bat, "bytes", len(new_raw.encode("cp1251")), "newline=CRLF")
