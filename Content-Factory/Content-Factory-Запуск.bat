@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 1251 >nul
cd /d "%~dp0"
title Content-Factory: Main Menu
set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"
%PY_CMD% --version >nul 2>nul || goto :NO_PYTHON
set "TTS_PY_CMD=%~dp0.venv-tts\Scripts\python.exe"
set "DEFAULT_STORIES_DIR=%~dp0stories\input"
set "DEFAULT_DEFERRED=runs\site\site-run-a\_phase_a\ready_queues\deferred.json"
set "PHASE_A_LIMIT_FILE=.orchestrator\phase_a_limit_mode.txt"
call :INIT_PHASE_A_LIMIT_MODE
:MAIN_MENU
cls
echo ============================================================
echo   Content-Factory: Main Menu
echo ============================================================
echo.
echo [1] Run full Site pipeline
echo [S] Full Site pipeline with Kokoro Google Drive TTS
echo [2] Run full YouTube pipeline
echo.
echo [3] Run individual Site stages
echo [4] Runtime modes menu
echo [5] Show current modes
echo [6] Run preflight
echo [7] Open reports folder
echo [8] Open logs and status files
echo [9] Cleanup / quarantine menu
echo [X] Run only Phase B ^(scaffold, not production^)
echo [R] Resume Site pipeline after manual steps
echo [T] Toggle Phase A limit ^(current: %PHASE_A_LIMIT_LABEL%^)
echo [K] Site TTS ^(Kokoro^): dry-run / queue
echo.
echo [0] Exit
echo.
set /p MAIN_CHOICE=Select menu item: 
if /I "%MAIN_CHOICE%"=="T" goto :TOGGLE_PHASE_A_LIMIT_MODE
if "%MAIN_CHOICE%"=="1" goto :RUN_SITE_PIPELINE
if /I "%MAIN_CHOICE%"=="S" goto :RUN_SITE_PIPELINE_KOKORO_DRIVE
if "%MAIN_CHOICE%"=="2" goto :RUN_YOUTUBE_PIPELINE
if "%MAIN_CHOICE%"=="3" goto :STAGE_MENU
if "%MAIN_CHOICE%"=="4" goto :MODES_MENU
if "%MAIN_CHOICE%"=="5" goto :SHOW_MODES
if /I "%MAIN_CHOICE%"=="K" goto :SITE_TTS_TEST_MENU
if "%MAIN_CHOICE%"=="6" goto :PREFLIGHT
if "%MAIN_CHOICE%"=="7" goto :OPEN_REPORTS
if "%MAIN_CHOICE%"=="8" goto :OPEN_LOGS
if "%MAIN_CHOICE%"=="9" goto :CLEANUP_MENU
if /I "%MAIN_CHOICE%"=="X" goto :RUN_SCAFFOLD_PHASE_B
if /I "%MAIN_CHOICE%"=="R" goto :RESUME_SITE_PIPELINE
if "%MAIN_CHOICE%"=="0" goto :EOF_EXIT
goto :MAIN_MENU

:RUN_SITE_PIPELINE
cls
echo [RUN] Site: Phase A -^> Phase B -^> orchestrator run site
echo.
set "GEMINI_WORKERS=5"
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set /p RUN_ID=Run id (Enter = site-run): 
if "%RUN_ID%"=="" set "RUN_ID=site-run"
echo.
echo Steps: [1/3] phase-a site  [2/3] phase-b  [3/3] run --pipeline site
choice /c YN /n /m "Confirm? [Y/N]: "
if errorlevel 2 goto :MAIN_MENU
echo.
set "PIPELINE_NAME=site"
set "PIPELINE_RUN_ID=%RUN_ID%"
set "PIPELINE_STATUS=running"
set "PIPELINE_REPORT_DIR=.orchestrator\reports\pipeline_runs"
set "PIPELINE_REPORT_FILE=%PIPELINE_REPORT_DIR%\%RUN_ID%_site.txt"
if not exist "%PIPELINE_REPORT_DIR%" mkdir "%PIPELINE_REPORT_DIR%"
(
  echo pipeline=site
  echo run_id=%RUN_ID%
  echo stories_dir=%STORIES_DIR%
  echo started=%DATE% %TIME%
) > "%PIPELINE_REPORT_FILE%"
echo [1/3] phase-a...
call :RESOLVE_SITE_VISUAL_MODE
if /I "%SITE_VISUAL_MODE%"=="auto" (
  if "%SITE_VISUAL_POD_URL%"=="" (
    echo [ERROR] visual auto: need ComfyUI/RunPod URL
    pause
    goto :MAIN_MENU
  )
  %PY_CMD% -m orchestrator phase-a --stories-dir "%STORIES_DIR%" --story-id "%RUN_ID%-a" --run-branch site --gemini-workers "%GEMINI_WORKERS%" %PHASE_A_MAX_STORIES_ARG% --visual-mode auto --visual-pod-url "%SITE_VISUAL_POD_URL%" --resume --execute
) else (
  %PY_CMD% -m orchestrator phase-a --stories-dir "%STORIES_DIR%" --story-id "%RUN_ID%-a" --run-branch site --gemini-workers "%GEMINI_WORKERS%" %PHASE_A_MAX_STORIES_ARG% --visual-mode manual --resume --execute
)
if errorlevel 1 (
  echo [ERROR] phase-a failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=phase-a"
  goto :SITE_PIPELINE_DONE
)
echo [OK] phase-a
call :PRINT_PHASE_A_STATS "%RUN_ID%-a"
echo [2/3] phase-b...
%PY_CMD% -m orchestrator phase-b --story-id "%RUN_ID%-b" --deferred-manifest "runs\site\%RUN_ID%-a\_phase_a\ready_queues\deferred.json" --gemini-registry "configs\gemini_bots_registry.example.yaml"
if errorlevel 1 (
  echo [ERROR] phase-b failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=phase-b"
  goto :SITE_PIPELINE_DONE
)
echo [OK] phase-b
echo [3/3] run site...
%PY_CMD% -m orchestrator run --pipeline site --story-id "%RUN_ID%-site" --stories-dir "%STORIES_DIR%" --execute
if errorlevel 1 (
  echo [ERROR] site run failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=site-runtime"
  goto :SITE_PIPELINE_DONE
)
echo [OK] site run
set "PIPELINE_STATUS=done"

:SITE_PIPELINE_DONE
if not "%PIPELINE_STATUS%"=="done" (
  echo.
  echo Stopped at: %PIPELINE_FAILED_STAGE%
)
call :EXPORT_RUN_RESULTS_TO_STORIES "%STORIES_DIR%" "%RUN_ID%"
call :WRITE_PIPELINE_REPORT
echo.
if "%PIPELINE_STATUS%"=="done" (
  echo Site pipeline OK.
) else (
  echo Site pipeline finished with errors.
)
echo Run id: %RUN_ID%
echo Report: %PIPELINE_REPORT_FILE%
pause
goto :MAIN_MENU

:RUN_SITE_PIPELINE_KOKORO_DRIVE
cls
echo [RUN] Site: FULL from input with Kokoro Google Drive TTS
echo.
set "GEMINI_WORKERS=5"
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set /p RUN_ID=Run id (Enter = site-drive-run): 
if "%RUN_ID%"=="" set "RUN_ID=site-drive-run"
echo.
echo Steps: [1/3] phase-a site  [2/3] phase-b site-only  [3/3] run --pipeline site ^(site_tts_engine=kokoro_colab_drive^)
choice /c YN /n /m "Confirm? [Y/N]: "
if errorlevel 2 goto :MAIN_MENU
echo.
set "PIPELINE_NAME=site"
set "PIPELINE_RUN_ID=%RUN_ID%"
set "PIPELINE_STATUS=running"
set "PIPELINE_REPORT_DIR=.orchestrator\reports\pipeline_runs"
set "PIPELINE_REPORT_FILE=%PIPELINE_REPORT_DIR%\%RUN_ID%_site_drive.txt"
if not exist "%PIPELINE_REPORT_DIR%" mkdir "%PIPELINE_REPORT_DIR%"
(
  echo pipeline=site
  echo run_id=%RUN_ID%
  echo stories_dir=%STORIES_DIR%
  echo tts_engine=kokoro_colab_drive
  echo started=%DATE% %TIME%
) > "%PIPELINE_REPORT_FILE%"
call :PRINT_SITE_PIPELINE_DIAGNOSTICS "%STORIES_DIR%" "kokoro_colab_drive"
echo [DIAG] SITE ONLY PIPELINE
echo [DIAG] phase_a=site
echo [DIAG] phase_b=site_only
echo [DIAG] pipeline=site
echo [DIAG] youtube_stages_enabled=false
echo [DIAG] tts_engine=kokoro_colab_drive
%PY_CMD% -m orchestrator set-mode --key site_tts_engine --value kokoro_colab_drive
if errorlevel 1 (
  echo [ERROR] failed to set site_tts_engine=kokoro_colab_drive
  pause
  goto :MAIN_MENU
)
echo [1/3] phase-a...
call :RESOLVE_SITE_VISUAL_MODE
if /I "%SITE_VISUAL_MODE%"=="auto" (
  if "%SITE_VISUAL_POD_URL%"=="" (
    echo [ERROR] visual auto: need ComfyUI/RunPod URL
    pause
    goto :MAIN_MENU
  )
  %PY_CMD% -m orchestrator phase-a --stories-dir "%STORIES_DIR%" --story-id "%RUN_ID%-a" --run-branch site --gemini-workers "%GEMINI_WORKERS%" %PHASE_A_MAX_STORIES_ARG% --visual-mode auto --visual-pod-url "%SITE_VISUAL_POD_URL%" --resume --execute
) else (
  %PY_CMD% -m orchestrator phase-a --stories-dir "%STORIES_DIR%" --story-id "%RUN_ID%-a" --run-branch site --gemini-workers "%GEMINI_WORKERS%" %PHASE_A_MAX_STORIES_ARG% --visual-mode manual --resume --execute
)
if errorlevel 1 (
  echo [ERROR] phase-a failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=phase-a"
  goto :SITE_PIPELINE_DONE
)
echo [OK] phase-a
call :PRINT_PHASE_A_STATS "%RUN_ID%-a"
echo [2/3] phase-b site-only...
%PY_CMD% -m orchestrator phase-b --branch site --story-id "%RUN_ID%-b" --deferred-manifest "runs\site\%RUN_ID%-a\_phase_a\ready_queues\deferred.json" --gemini-registry "configs\gemini_bots_registry.example.yaml"
if errorlevel 1 (
  echo [ERROR] phase-b failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=phase-b"
  goto :SITE_PIPELINE_DONE
)
echo [OK] phase-b
call :CHECK_PREPARED_SITE_OUTPUT_OR_FAIL
if errorlevel 1 (
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=site-preparation-check"
  goto :SITE_PIPELINE_DONE
)
echo [3/3] run site...
%PY_CMD% -m orchestrator run --pipeline site --story-id "%RUN_ID%-site" --stories-dir "%STORIES_DIR%" --execute
if errorlevel 1 (
  echo [ERROR] site run failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=site-runtime"
  goto :SITE_PIPELINE_DONE
)
echo [OK] site run
set "PIPELINE_STATUS=done"
goto :SITE_PIPELINE_DONE

:RUN_YOUTUBE_PIPELINE
cls
echo [RUN] YouTube: Phase A -^> Phase B -^> orchestrator run youtube
echo.
set "GEMINI_WORKERS=5"
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set /p RUN_ID=Run id (Enter = youtube-run): 
if "%RUN_ID%"=="" set "RUN_ID=youtube-run"
echo.
echo Steps: [1/3] phase-a youtube  [2/3] phase-b  [3/3] run --pipeline youtube
choice /c YN /n /m "Confirm? [Y/N]: "
if errorlevel 2 goto :MAIN_MENU
echo.
set "PIPELINE_NAME=youtube"
set "PIPELINE_RUN_ID=%RUN_ID%"
set "PIPELINE_STATUS=running"
set "PIPELINE_REPORT_DIR=.orchestrator\reports\pipeline_runs"
set "PIPELINE_REPORT_FILE=%PIPELINE_REPORT_DIR%\%RUN_ID%_youtube.txt"
if not exist "%PIPELINE_REPORT_DIR%" mkdir "%PIPELINE_REPORT_DIR%"
(
  echo pipeline=youtube
  echo run_id=%RUN_ID%
  echo stories_dir=%STORIES_DIR%
  echo started=%DATE% %TIME%
) > "%PIPELINE_REPORT_FILE%"
echo [1/3] phase-a...
%PY_CMD% -m orchestrator phase-a --stories-dir "%STORIES_DIR%" --story-id "%RUN_ID%-a" --run-branch youtube --gemini-workers "%GEMINI_WORKERS%" %PHASE_A_MAX_STORIES_ARG% --resume --execute
if errorlevel 1 (
  echo [ERROR] phase-a failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=phase-a"
  goto :YOUTUBE_PIPELINE_DONE
)
echo [OK] phase-a
call :PRINT_PHASE_A_STATS "%RUN_ID%-a"
echo [2/3] phase-b...
%PY_CMD% -m orchestrator phase-b --story-id "%RUN_ID%-b" --deferred-manifest "runs\youtube\%RUN_ID%-a\_phase_a\ready_queues\deferred.json" --gemini-registry "configs\gemini_bots_registry.example.yaml"
if errorlevel 1 (
  echo [ERROR] phase-b failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=phase-b"
  goto :YOUTUBE_PIPELINE_DONE
)
echo [OK] phase-b
echo [3/3] run youtube...
%PY_CMD% -m orchestrator run --pipeline youtube --story-id "%RUN_ID%-youtube" --stories-dir "%STORIES_DIR%" --execute
if errorlevel 1 (
  echo [ERROR] youtube run failed
  set "PIPELINE_STATUS=failed"
  set "PIPELINE_FAILED_STAGE=youtube-runtime"
  goto :YOUTUBE_PIPELINE_DONE
)
echo [OK] youtube run
set "PIPELINE_STATUS=done"

:YOUTUBE_PIPELINE_DONE
if not "%PIPELINE_STATUS%"=="done" (
  echo.
  echo Stopped at: %PIPELINE_FAILED_STAGE%
)
call :EXPORT_RUN_RESULTS_TO_STORIES "%STORIES_DIR%" "%RUN_ID%"
call :WRITE_PIPELINE_REPORT
echo.
if "%PIPELINE_STATUS%"=="done" (
  echo YouTube pipeline OK.
) else (
  echo YouTube pipeline finished with errors.
)
echo Run id: %RUN_ID%
echo Report: %PIPELINE_REPORT_FILE%
pause
goto :MAIN_MENU

:STAGE_MENU
cls
echo ============================================================
echo   Technical stages
echo ============================================================
echo.
echo [1] filter-length
echo [2] phase-b ^(deferred manifest^)
echo [0] back
echo.
set /p STAGE_CHOICE=Choice: 
if "%STAGE_CHOICE%"=="0" goto :MAIN_MENU
if "%STAGE_CHOICE%"=="1" goto :RUN_LENGTH
if "%STAGE_CHOICE%"=="2" goto :RUN_PHASE_B
goto :STAGE_MENU

:RUN_LENGTH
cls
echo filter-length
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :STAGE_MENU
%PY_CMD% -m orchestrator filter-length --stories-dir "%STORIES_DIR%" --execute
pause
goto :STAGE_MENU

:RUN_PHASE_B
cls
echo phase-b
set /p DEFERRED=deferred.json (Enter = %DEFAULT_DEFERRED%): 
if "%DEFERRED%"=="" set "DEFERRED=%DEFAULT_DEFERRED%"
if not exist "%DEFERRED%" (
  echo [ERROR] not found: "%DEFERRED%"
  pause
  goto :STAGE_MENU
)
set /p B_ID=phase-b story-id (Enter = phaseb-manual): 
if "%B_ID%"=="" set "B_ID=phaseb-manual"
%PY_CMD% -m orchestrator phase-b --story-id "%B_ID%" --deferred-manifest "%DEFERRED%" --gemini-registry "configs\gemini_bots_registry.example.yaml"
pause
goto :STAGE_MENU

:CLEANUP_MENU
cls
echo ============================================================
echo   Cleanup / quarantine
echo ============================================================
echo.
echo [1] cleanup-scan dry-run
echo [2] cleanup-move ^(comma paths^)
echo [3] cleanup-run by run_id
echo [0] back
echo.
set /p CLEAN_CHOICE=Choice: 
if "%CLEAN_CHOICE%"=="0" goto :MAIN_MENU
if "%CLEAN_CHOICE%"=="1" goto :CLEANUP_SCAN
if "%CLEAN_CHOICE%"=="2" goto :CLEANUP_MOVE
if "%CLEAN_CHOICE%"=="3" goto :CLEANUP_MOVE_RUN
goto :CLEANUP_MENU

:CLEANUP_SCAN
cls
%PY_CMD% -m orchestrator cleanup-scan --root "."
pause
goto :CLEANUP_MENU

:CLEANUP_MOVE
cls
set /p CLEAN_PATHS=Paths (comma-separated): 
if "%CLEAN_PATHS%"=="" goto :CLEANUP_MENU
choice /c YN /n /m "Move to quarantine? [Y/N]: "
if errorlevel 2 goto :CLEANUP_MENU
%PY_CMD% -m orchestrator cleanup-move --root "." --paths "%CLEAN_PATHS%"
pause
goto :CLEANUP_MENU

:CLEANUP_MOVE_RUN
cls
set /p CLEAN_RUN_ID=run_id: 
if "%CLEAN_RUN_ID%"=="" goto :CLEANUP_MENU
choice /c YN /n /m "Quarantine runs/%CLEAN_RUN_ID% ? [Y/N]: "
if errorlevel 2 goto :CLEANUP_MENU
%PY_CMD% -m orchestrator cleanup-run --root "." --run-id "%CLEAN_RUN_ID%"
pause
goto :CLEANUP_MENU

:RUN_SCAFFOLD_PHASE_B
cls
echo phase-b --allow-scaffold ^(NOT production^)
set /p DEFERRED=deferred.json (Enter = %DEFAULT_DEFERRED%): 
if "%DEFERRED%"=="" set "DEFERRED=%DEFAULT_DEFERRED%"
if not exist "%DEFERRED%" (
  echo [ERROR] not found: "%DEFERRED%"
  pause
  goto :MAIN_MENU
)
set /p B_ID=story-id (Enter = phaseb-scaffold): 
if "%B_ID%"=="" set "B_ID=phaseb-scaffold"
%PY_CMD% -m orchestrator phase-b --story-id "%B_ID%" --deferred-manifest "%DEFERRED%" --gemini-registry "configs\gemini_bots_registry.example.yaml" --allow-scaffold
pause
goto :MAIN_MENU

:RESUME_SITE_PIPELINE
cls
echo Resume: orchestrator run site only ^(after manual steps^)
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set /p RUN_ID=Run id (e.g. site-run): 
if "%RUN_ID%"=="" goto :MAIN_MENU
%PY_CMD% -m orchestrator run --pipeline site --story-id "%RUN_ID%-site" --stories-dir "%STORIES_DIR%" --execute
pause
goto :MAIN_MENU

:SITE_TTS_TEST_MENU
if not exist "%TTS_PY_CMD%" (
  echo [ERROR] TTS Python not found: %TTS_PY_CMD%
  echo Site TTS requires .venv-tts.
  pause
  goto :MAIN_MENU
)
cls
for /f %%I in ('dir /b /ad "output\site" 2^>nul ^| find /c /v ""') do set "PREPARED_SITE_COUNT=%%I"
if "%PREPARED_SITE_COUNT%"=="" set "PREPARED_SITE_COUNT=0"
if "%PREPARED_SITE_COUNT%"=="0" (
  echo No prepared output/site stories found.
  echo This is a TTS-only command.
  echo For raw input stories, run:
  echo [S] Full Site pipeline with Kokoro Google Drive TTS
  pause
  goto :MAIN_MENU
)
echo ============================================================
echo   Site TTS tools ^(prepared output\site only^)
echo ============================================================
echo Queue: cleaned_story.txt, no folder.mp3, voice M/F/U from info.txt
echo Dry-run first. Modes: main [4] then [3] for Kokoro + local.
echo.
echo [1] scan queue
echo [2] dry-run sync ^(no mp3^)
echo [3] execute sync ^(writes mp3; existing mp3 skipped^)
echo [4] sync first N ^(dry-run then optional execute^)
echo [5] export Kokoro Colab batch
echo [6] import Kokoro Colab results
echo [7] verify mp3 coverage
echo [8] [TTS only] Prepared output/site stories -^> Google Drive -^> wait mp3
echo [9] setup Google Drive Kokoro workspace
echo [0] back
echo.
set /p KCH=Choice: 
if "%KCH%"=="0" goto :MAIN_MENU
if "%KCH%"=="1" (
  "%TTS_PY_CMD%" -m orchestrator site-tts scan
  pause
  goto :SITE_TTS_TEST_MENU
)
if "%KCH%"=="2" (
  "%TTS_PY_CMD%" -m orchestrator site-tts sync
  pause
  goto :SITE_TTS_TEST_MENU
)
if "%KCH%"=="3" (
  echo Writes mp3 under output\site\...
  choice /c YN /n /m "Continue? [Y/N]: "
  if errorlevel 2 goto :SITE_TTS_TEST_MENU
  echo Checking Kokoro in .venv-tts...
  "%TTS_PY_CMD%" -c "import sys; print('TTS Python:', sys.executable); import kokoro; print('kokoro import OK')"
  if errorlevel 1 (
    echo [ERROR] Kokoro check failed. sync --execute not started.
    pause
    goto :SITE_TTS_TEST_MENU
  )
  "%TTS_PY_CMD%" -m orchestrator site-tts sync --execute
  pause
  goto :SITE_TTS_TEST_MENU
)
if "%KCH%"=="4" (
  set /p TTS_N=Limit N: 
  if "%TTS_N%"=="" goto :SITE_TTS_TEST_MENU
  echo Dry-run first N=%TTS_N% ...
  "%TTS_PY_CMD%" -m orchestrator site-tts sync --limit %TTS_N%
  choice /c YN /n /m "Execute mp3 for these N? [Y/N]: "
  if errorlevel 2 goto :SITE_TTS_TEST_MENU
  echo Checking Kokoro in .venv-tts...
  "%TTS_PY_CMD%" -c "import sys; print('TTS Python:', sys.executable); import kokoro; print('kokoro import OK')"
  if errorlevel 1 (
    echo [ERROR] Kokoro check failed. sync --limit --execute not started.
    pause
    goto :SITE_TTS_TEST_MENU
  )
  "%TTS_PY_CMD%" -m orchestrator site-tts sync --limit %TTS_N% --execute
  pause
  goto :SITE_TTS_TEST_MENU
)
if "%KCH%"=="5" goto :SITE_TTS_COLAB_EXPORT
if "%KCH%"=="6" goto :SITE_TTS_COLAB_IMPORT
if "%KCH%"=="7" goto :SITE_TTS_COLAB_VERIFY
if "%KCH%"=="8" goto :SITE_TTS_COLAB_FULL_DRIVE
if "%KCH%"=="9" goto :SITE_TTS_COLAB_SETUP_DRIVE
goto :SITE_TTS_TEST_MENU

:SITE_TTS_COLAB_EXPORT
cls
set /p KC_LIMIT=Export limit ^(0=all, Enter=100^): 
if "%KC_LIMIT%"=="" set "KC_LIMIT=100"
set /p KC_BATCH=Batch id ^(optional^): 
if "%KC_BATCH%"=="" (
  "%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab export --limit %KC_LIMIT%
) else (
  "%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab export --limit %KC_LIMIT% --batch-id "%KC_BATCH%"
)
pause
goto :SITE_TTS_TEST_MENU

:SITE_TTS_COLAB_IMPORT
cls
set /p KC_BATCH=Batch id ^(or leave empty to use batch dir^): 
set /p KC_DIR=Batch dir ^(optional^): 
choice /c YN /n /m "Force overwrite existing mp3? [Y/N]: "
set "KC_FORCE="
if errorlevel 2 set "KC_FORCE="
if errorlevel 1 set "KC_FORCE=--force"
if not "%KC_DIR%"=="" (
  "%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab import --batch-dir "%KC_DIR%" %KC_FORCE%
) else (
  if "%KC_BATCH%"=="" (
    echo [ERROR] provide batch-id or batch-dir
    pause
    goto :SITE_TTS_TEST_MENU
  )
  "%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab import --batch-id "%KC_BATCH%" %KC_FORCE%
)
pause
goto :SITE_TTS_TEST_MENU

:SITE_TTS_COLAB_VERIFY
cls
set /p KC_BATCH=Batch id ^(optional^): 
if "%KC_BATCH%"=="" (
  "%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab verify
) else (
  "%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab verify --batch-id "%KC_BATCH%"
)
pause
goto :SITE_TTS_TEST_MENU

:SITE_TTS_COLAB_SETUP_DRIVE
cls
echo Google Drive setup: create folders + copy Colab runner script
"%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab setup-drive
pause
goto :SITE_TTS_TEST_MENU

:SITE_TTS_COLAB_FULL_DRIVE
cls
echo [TTS only] Prepared output/site stories ^> Google Drive ^> wait mp3 ^> import ^> cleanup
set /p KC_LIMIT=Limit stories ^(0=all, Enter=0^): 
if "%KC_LIMIT%"=="" set "KC_LIMIT=0"
set /p KC_WAIT=Wait interval minutes ^(Enter=config^): 
set /p KC_MAX=Max wait hours ^(Enter=config^): 
set "KC_WAIT_ARG="
set "KC_MAX_ARG="
if not "%KC_WAIT%"=="" set "KC_WAIT_ARG=--wait-interval-minutes %KC_WAIT%"
if not "%KC_MAX%"=="" set "KC_MAX_ARG=--max-wait-hours %KC_MAX%"
choice /c YN /n /m "Force overwrite existing mp3? [Y/N]: "
set "KC_FORCE="
if errorlevel 1 set "KC_FORCE=--force"
if errorlevel 2 set "KC_FORCE="
"%TTS_PY_CMD%" -m orchestrator site-tts kokoro-colab full-cycle-drive --limit %KC_LIMIT% %KC_WAIT_ARG% %KC_MAX_ARG% %KC_FORCE%
pause
goto :SITE_TTS_TEST_MENU

:MODES_MENU
cls
echo ============================================================
echo   Runtime modes
echo ============================================================
echo.
echo [1] Site visual
echo [2] YouTube publish
echo [3] Site TTS ^(runtime + engine^)
echo [4] YouTube TTS ^(runtime + engine^)
echo [5] ElevenLabs mode
echo [6] Video build
echo [7] show-modes
echo [8] reset-modes
echo [0] back
echo.
set /p MODE_CHOICE=Choice: 
if "%MODE_CHOICE%"=="0" goto :MAIN_MENU
if "%MODE_CHOICE%"=="1" goto :SET_SITE_VISUAL
if "%MODE_CHOICE%"=="2" goto :SET_YT_PUBLISH
if "%MODE_CHOICE%"=="3" goto :SET_SITE_TTS
if "%MODE_CHOICE%"=="4" goto :SET_YT_TTS
if "%MODE_CHOICE%"=="5" goto :SET_ELEVEN_MODE
if "%MODE_CHOICE%"=="6" goto :SET_VIDEO_BUILD
if "%MODE_CHOICE%"=="7" goto :SHOW_MODES
if "%MODE_CHOICE%"=="8" goto :RESET_MODES
goto :MODES_MENU


:SET_SITE_VISUAL
echo Select site visual mode:
echo [1] Auto
echo [2] Manual
set /p CH=Your choice: 
if "%CH%"=="1" %PY_CMD% -m orchestrator set-mode --key site_visual --value auto
if "%CH%"=="2" %PY_CMD% -m orchestrator set-mode --key site_visual --value manual
pause
goto :MODES_MENU
:SET_YT_PUBLISH
echo Select YouTube publish mode:
echo [1] Publish via API
echo [2] Manual
set /p CH=Your choice: 
if "%CH%"=="1" %PY_CMD% -m orchestrator set-mode --key youtube_publish --value api
if "%CH%"=="2" %PY_CMD% -m orchestrator set-mode --key youtube_publish --value manual
pause
goto :MODES_MENU
:SET_SITE_TTS
echo Configure Site TTS ^(configs/runtime_modes.yaml + configs/site_tts.yaml^):
echo Runtime: [1] local ^(Kokoro^) [2] disabled [3] RunPod ^(Fish etc.^)
set /p RCH=Your runtime choice: 
if "%RCH%"=="1" %PY_CMD% -m orchestrator set-mode --key site_tts_runtime --value local
if "%RCH%"=="2" %PY_CMD% -m orchestrator set-mode --key site_tts_runtime --value disabled
if "%RCH%"=="3" %PY_CMD% -m orchestrator set-mode --key site_tts_runtime --value runpod
echo Engine: [1] Kokoro [2] ElevenLabs ^(legacy^) [3] Fish S2 Pro ^(RunPod^) [4] Kokoro Colab Drive auto-wait
set /p ECH=Your engine choice: 
if "%ECH%"=="1" %PY_CMD% -m orchestrator set-mode --key site_tts_engine --value kokoro
if "%ECH%"=="2" %PY_CMD% -m orchestrator set-mode --key site_tts_engine --value elevenlabs
if "%ECH%"=="3" %PY_CMD% -m orchestrator set-mode --key site_tts_engine --value fish_audio_s2_pro
if "%ECH%"=="4" %PY_CMD% -m orchestrator set-mode --key site_tts_engine --value kokoro_colab_drive
echo.
echo Kokoro voice settings: configs\site_tts.yaml
pause
goto :MODES_MENU
:SET_YT_TTS
echo Configure YouTube TTS:
echo Runtime: [1] local [2] Colab
set /p RCH=Your runtime choice: 
if "%RCH%"=="1" %PY_CMD% -m orchestrator set-mode --key youtube_tts_runtime --value local
if "%RCH%"=="2" %PY_CMD% -m orchestrator set-mode --key youtube_tts_runtime --value colab
echo Engine: [1] ElevenLabs [2] Fish Audio [3] Microsoft Edge TTS
set /p ECH=Your engine choice: 
if "%ECH%"=="1" %PY_CMD% -m orchestrator set-mode --key youtube_tts_engine --value elevenlabs
if "%ECH%"=="2" %PY_CMD% -m orchestrator set-mode --key youtube_tts_engine --value fish_audio
if "%ECH%"=="3" %PY_CMD% -m orchestrator set-mode --key youtube_tts_engine --value edge_tts
pause
goto :MODES_MENU
:SET_ELEVEN_MODE
echo Select ElevenLabs mode:
echo [1] Normal
echo [2] Free-keys mode
set /p CH=Your choice: 
if "%CH%"=="1" %PY_CMD% -m orchestrator set-mode --key elevenlabs_mode --value normal
if "%CH%"=="2" %PY_CMD% -m orchestrator set-mode --key elevenlabs_mode --value free_keys
pause
goto :MODES_MENU
:SET_VIDEO_BUILD
echo Configure video build mode:
echo [1] Local
echo [2] Colab
echo [3] RunPod
set /p CH=Your choice: 
if "%CH%"=="1" %PY_CMD% -m orchestrator set-mode --key video_build --value local
if "%CH%"=="2" %PY_CMD% -m orchestrator set-mode --key video_build --value colab
if "%CH%"=="3" %PY_CMD% -m orchestrator set-mode --key video_build --value runpod
pause
goto :MODES_MENU
:SHOW_MODES
cls
echo Current modes ^(console^):
%PY_CMD% -m orchestrator show-modes
echo.
pause
goto :MAIN_MENU
:RESET_MODES
%PY_CMD% -m orchestrator reset-modes
pause
goto :MODES_MENU
:PREFLIGHT
cls
echo Running environment preflight.
%PY_CMD% -m orchestrator preflight --pipeline full --run-profile dry-run-all --stories-dir "%DEFAULT_STORIES_DIR%"
echo.
pause
goto :MAIN_MENU
:OPEN_REPORTS
if not exist ".orchestrator\reports" mkdir ".orchestrator\reports"
start "" ".orchestrator\reports"
goto :MAIN_MENU
:OPEN_LOGS
if not exist ".orchestrator" mkdir ".orchestrator"
if not exist ".orchestrator\reports" mkdir ".orchestrator\reports"
if not exist ".orchestrator\reports\pipeline_runs" mkdir ".orchestrator\reports\pipeline_runs"
if not exist ".orchestrator\status.jsonl" type nul > ".orchestrator\status.jsonl"
if not exist ".orchestrator\events.jsonl" type nul > ".orchestrator\events.jsonl"
start "" ".orchestrator"
start "" ".orchestrator\status.jsonl"
start "" ".orchestrator\events.jsonl"
start "" ".orchestrator\reports"
start "" ".orchestrator\reports\pipeline_runs"
goto :MAIN_MENU
:EXPORT_RUN_RESULTS_TO_STORIES
REM Legacy export disabled: do not write any _results into stories/input/.
goto :EOF
:WRITE_PIPELINE_REPORT
(
  echo finished=%DATE% %TIME%
  echo status=%PIPELINE_STATUS%
  if "%PIPELINE_STATUS%"=="failed" echo failed_stage=%PIPELINE_FAILED_STAGE%
  echo.
  if /I "%PIPELINE_NAME%"=="youtube" (
    echo artifacts_phase_a=runs\youtube\%PIPELINE_RUN_ID%-a\_phase_a
    echo artifacts_phase_b=runs\youtube\%PIPELINE_RUN_ID%-a\_phase_b
  ) else (
    echo artifacts_phase_a=runs\site\%PIPELINE_RUN_ID%-a\_phase_a
    echo artifacts_phase_b=runs\site\%PIPELINE_RUN_ID%-a\_phase_b
  )
  if /I "%PIPELINE_NAME%"=="youtube" (
    echo run_root=runs\youtube\%PIPELINE_RUN_ID%-a
  ) else (
    echo run_root=runs\site\%PIPELINE_RUN_ID%-a
  )
) >> "%PIPELINE_REPORT_FILE%"
set "PIPELINE_FAILED_STAGE="
goto :EOF
:PRINT_SITE_PREP_RESULT
set "SITE_RUN_ID=%~1"
if "%SITE_RUN_ID%"=="" goto :EOF
set "SITE_MANIFEST=runs\site\%SITE_RUN_ID%\manifest.json"
if not exist "%SITE_MANIFEST%" (
  echo [WARN] Site preparation manifest not found: "%SITE_MANIFEST%"
  goto :EOF
)
%PY_CMD% -c "import json,sys; p=sys.argv[1]; d=json.load(open(p,'r',encoding='utf-8')); print('========================================'); print('Site preparation summary'); print('========================================'); print('status: site_preparation_done'); print('run folder: runs/site/' + str(d.get('run_id','<run_id>')) + '/'); print('output folder: output/site/'); print('accepted_in_output: ' + str(d.get('accepted_in_output',0))); print('rejected: ' + str(d.get('rejected',0))); print('manual_review: ' + str(d.get('manual_review',0))); print('stage_stop: ' + str(d.get('stage_stop','waiting_next_real_stage'))); print('========================================')" "%SITE_MANIFEST%"
set "SITE_RUN_ID="
set "SITE_MANIFEST="
goto :EOF
:FULL_PROD_NOT_CONNECTED
cls
echo Full Gemini production chain is not connected here. Run Phase A, Phase B and next stages first.
echo.
pause
goto :MAIN_MENU
:NO_PYTHON
echo Python is not found in PATH.
echo Install Python 3.11+ and retry.
pause
exit /b 1
:ASK_STORIES_DIR
set "STORIES_DIR=%DEFAULT_STORIES_DIR%"
if not exist "%STORIES_DIR%" (
  mkdir "%STORIES_DIR%" >nul 2>nul
)
if not exist "%STORIES_DIR%" (
  echo [ERROR] Folder was not created: "%STORIES_DIR%"
  set "STORIES_DIR="
  pause
  goto :EOF
)
set "TXT_COUNT="
for /f %%I in ('dir /b /a:-d "%STORIES_DIR%\*.txt" 2^>nul ^| find /c /v ""') do set "TXT_COUNT=%%I"
if "%TXT_COUNT%"=="" set "TXT_COUNT=0"
echo Current stories directory: stories/input/
echo Found txt files: %TXT_COUNT%
if "%TXT_COUNT%"=="0" (
echo No raw input stories found. Put stories into: %STORIES_DIR%
  set "STORIES_DIR="
  pause
)
goto :EOF
:PRINT_SITE_PIPELINE_DIAGNOSTICS
set "DIAG_STORIES_DIR=%~1"
set "DIAG_TTS_ENGINE=%~2"
for /f %%I in ('dir /b /a:-d "%DIAG_STORIES_DIR%\*.txt" 2^>nul ^| find /c /v ""') do set "DIAG_RAW_COUNT=%%I"
if "%DIAG_RAW_COUNT%"=="" set "DIAG_RAW_COUNT=0"
echo [DIAG] input_dir=%DIAG_STORIES_DIR%
echo [DIAG] raw_files_found=%DIAG_RAW_COUNT%
echo [DIAG] output_site_dir=output\site
for /f "usebackq delims=" %%I in (`%PY_CMD% -c "from pathlib import Path; from orchestrator.site_tts.config import load_site_tts_settings as L; s=L(Path('.')); print(s.google_drive_root_dir or '(not_configured)')"`) do set "DIAG_GDRIVE_ROOT=%%I"
echo [DIAG] google_drive_root=%DIAG_GDRIVE_ROOT%
echo [DIAG] site_tts_engine=%DIAG_TTS_ENGINE%
echo [DIAG] publish_enabled=true
echo [DIAG] mode=execute
set "DIAG_STORIES_DIR="
set "DIAG_TTS_ENGINE="
set "DIAG_RAW_COUNT="
set "DIAG_GDRIVE_ROOT="
goto :EOF
:CHECK_PREPARED_SITE_OUTPUT_OR_FAIL
set "PREPARED_SITE_COUNT="
for /f %%I in ('dir /b /ad "output\site" 2^>nul ^| find /c /v ""') do set "PREPARED_SITE_COUNT=%%I"
if "%PREPARED_SITE_COUNT%"=="" set "PREPARED_SITE_COUNT=0"
if "%PREPARED_SITE_COUNT%"=="0" (
  echo [ERROR] Site preparation produced 0 prepared stories. TTS was not started.
  exit /b 1
)
exit /b 0
:RESOLVE_SITE_VISUAL_MODE
set "SITE_VISUAL_MODE="
set "SITE_VISUAL_POD_URL="
for /f "usebackq delims=" %%I in (`%PY_CMD% -c "from pathlib import Path; from orchestrator.runtime_modes import load_runtime_modes, DEFAULT_MODES; m=load_runtime_modes(Path('configs/runtime_modes.yaml')); print(m.get('site_visual', DEFAULT_MODES['site_visual']))"`) do set "SITE_VISUAL_MODE=%%I"
if /I not "%SITE_VISUAL_MODE%"=="auto" set "SITE_VISUAL_MODE=manual"
if /I "%SITE_VISUAL_MODE%"=="auto" (
  echo [INFO] visual: auto
  set /p SITE_VISUAL_POD_URL=ComfyUI/RunPod URL: 
) else (
  echo [INFO] visual: manual
)
goto :EOF
:PRINT_PHASE_A_STATS
set "PHASE_A_STATS_RUN_ID=%~1"
set "PHASE_A_SUMMARY_FILE=runs\site\%PHASE_A_STATS_RUN_ID%\_phase_a\phase_a_summary.json"
if not exist "%PHASE_A_SUMMARY_FILE%" set "PHASE_A_SUMMARY_FILE=runs\youtube\%PHASE_A_STATS_RUN_ID%\_phase_a\phase_a_summary.json"
if not exist "%PHASE_A_SUMMARY_FILE%" (
  echo [WARN] Phase A summary not found: runs\site\ or runs\youtube\ ...\%PHASE_A_STATS_RUN_ID%\_phase_a\phase_a_summary.json
  goto :EOF
)
echo.
echo -------------------- Phase A: summary --------------------
%PY_CMD% -c "import json,sys; p=sys.argv[1]; d=json.load(open(p,'r',encoding='utf-8')); s=d.get('stats',{}); print(f\"intake_total={s.get('intake_total','n/a')}\"); print(f\"short_rejected={s.get('short_rejected_total','n/a')}\"); print(f\"selected_yes={s.get('selected_pending_gemini','n/a')}\"); print(f\"rejected_no={s.get('rejected_gemini','n/a')}\"); print(f\"manual_review={s.get('manual_review_gemini','n/a')}\"); print(f\"cleaned_total={s.get('cleaned_total','n/a')}\"); print(f\"deferred_total={s.get('deferred_total','n/a')}\"); print(f\"skipped_test_limit={s.get('skipped_test_limit','n/a')}\")" "%PHASE_A_SUMMARY_FILE%"
if errorlevel 1 (
  echo [WARN] Could not parse stats from "%PHASE_A_SUMMARY_FILE%"
)
echo summary_file=%PHASE_A_SUMMARY_FILE%
if exist "runs\site\%PHASE_A_STATS_RUN_ID%\selection_index.json" (
  echo selection_index=runs\site\%PHASE_A_STATS_RUN_ID%\selection_index.json
  echo report_md=runs\site\%PHASE_A_STATS_RUN_ID%\REPORT.md
) else if exist "runs\youtube\%PHASE_A_STATS_RUN_ID%\selection_index.json" (
  echo selection_index=runs\youtube\%PHASE_A_STATS_RUN_ID%\selection_index.json
  echo report_md=runs\youtube\%PHASE_A_STATS_RUN_ID%\REPORT.md
)
echo ---------------------------------------------------------
echo.
goto :EOF
:INIT_PHASE_A_LIMIT_MODE
if not exist ".orchestrator" mkdir ".orchestrator"
set "PHASE_A_LIMIT_MODE=TEST"
if exist "%PHASE_A_LIMIT_FILE%" (
  for /f "usebackq tokens=* delims=" %%I in ("%PHASE_A_LIMIT_FILE%") do set "PHASE_A_LIMIT_MODE=%%I"
)
if /I "%PHASE_A_LIMIT_MODE%"=="FULL" (
  set "PHASE_A_LIMIT_MODE=FULL"
  set "PHASE_A_LIMIT_LABEL=FULL (entire queue)"
  set "PHASE_A_MAX_STORIES_ARG="
) else (
  set "PHASE_A_LIMIT_MODE=TEST"
  set "PHASE_A_LIMIT_LABEL=TEST (50)"
  set "PHASE_A_MAX_STORIES_ARG=--max-stories 50"
)
goto :EOF
:TOGGLE_PHASE_A_LIMIT_MODE
if /I "%PHASE_A_LIMIT_MODE%"=="FULL" (
  set "PHASE_A_LIMIT_MODE=TEST"
) else (
  set "PHASE_A_LIMIT_MODE=FULL"
)
if /I "%PHASE_A_LIMIT_MODE%"=="FULL" (
  > "%PHASE_A_LIMIT_FILE%" echo FULL
) else (
  > "%PHASE_A_LIMIT_FILE%" echo TEST
)
call :INIT_PHASE_A_LIMIT_MODE
echo.
echo [INFO] Phase A mode switched: %PHASE_A_LIMIT_LABEL%
goto :MAIN_MENU
:EOF_EXIT
echo Exiting.
exit /b 0
