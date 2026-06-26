@echo off
rem Explorer / ?????: bat ????? ???????? ??? "cmd /c" ? ??? exit ???? ????????????. ????? ??? cmd /k ???????? ????????.
if /I not "%~1"=="__CF_KEEP_OPEN" (
  "%ComSpec%" /k call "%~f0" __CF_KEEP_OPEN %*
  exit /b 0
)
setlocal EnableExtensions DisableDelayedExpansion
shift /1
chcp 1251 >nul
cd /d "%~dp0"
title Content-Factory - Main menu
set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"
%PY_CMD% --version >nul 2>nul || goto :NO_PYTHON
set "TTS_PY_CMD=%~dp0.venv-tts\Scripts\python.exe"
set "DEFAULT_STORIES_DIR=%~dp0stories\input"
set "DEFAULT_DEFERRED=runs\site\site-run-a\_phase_a\ready_queues\deferred.json"
set "PHASE_A_LIMIT_FILE=.orchestrator\phase_a_limit_mode.txt"
call :INIT_PHASE_A_LIMIT_MODE
call :INIT_LIBRARY_SOURCE_DIR
:MAIN_MENU
cls
echo ============================================================
echo   Content-Factory - Main menu
echo ============================================================
echo.
echo [1] [LEGACY / DANGEROUS] Site partial run (global runs/output)
echo [2] Site full run to Zapuski folder
echo [3] Site: continue isolated launch (resume)
echo [4] Site: technical stages
echo [5] Site: TTS -^> MP3 (Kokoro Colab / Drive)
echo [6] YouTube: full pipeline
echo [7] YouTube: stages (see note vs [6])
echo [8] Checks, reports, logs
echo [9] Service / runtime / cleanup
echo [Y] YouTube Visuals
echo [V] YouTube video production / 10 Colab
echo [Q] Sample library -^> stories\input
echo [0] Exit
echo.
set /p MAIN_CHOICE=Select [0-9 Y V Q]: 
set "MAIN_CHOICE=%MAIN_CHOICE: =%"
if /I "%MAIN_CHOICE%"=="Q" goto :STORIES_INPUT_MENU
if /I "%MAIN_CHOICE%"=="Y" goto :YT_VISUALS_MENU
if /I "%MAIN_CHOICE%"=="V" goto :YT_VIDEO_PRODUCTION_MENU
set "MAIN_CHOICE=%MAIN_CHOICE:~0,1%"
if "%MAIN_CHOICE%"=="1" goto :RUN_SITE_PARTIAL
if "%MAIN_CHOICE%"=="2" goto :RUN_SITE_FULL
if "%MAIN_CHOICE%"=="3" goto :RUN_SITE_LAUNCH_RESUME
if "%MAIN_CHOICE%"=="4" goto :STAGE_MENU
if "%MAIN_CHOICE%"=="5" goto :SITE_TTS_TEST_MENU
if "%MAIN_CHOICE%"=="6" goto :RUN_YOUTUBE_PIPELINE
if "%MAIN_CHOICE%"=="7" goto :YT_STAGES_MENU
if "%MAIN_CHOICE%"=="8" goto :CHECKS_MENU
if "%MAIN_CHOICE%"=="9" goto :SERVICE_MENU
if "%MAIN_CHOICE%"=="0" goto :EOF_EXIT
echo [WARN] Unknown choice: "%MAIN_CHOICE%"
timeout /t 1 /nobreak >nul
goto :MAIN_MENU
:YT_VIDEO_PRODUCTION_MENU
cls
echo ============================================================
echo   YouTube video production / 10 Colab
echo ============================================================
echo.
echo [1] Start watcher only
echo [2] Open Yandex Colab worker tabs
echo [3] Open Chrome Colab worker tabs
echo [4] Open all Colab worker tabs
echo [5] Queue status
echo [6] Validate job assets
echo [7] Cleanup partial checkpoints
echo [8] Browser profiles diagnostic
echo [0] Back
echo.
set /p YTVP_CHOICE=Choice: 
if "%YTVP_CHOICE%"=="0" goto :MAIN_MENU
if "%YTVP_CHOICE%"=="1" call "%~dp0START_YOUTUBE_VIDEO_WATCHER.bat" & goto :YT_VIDEO_PRODUCTION_MENU
if "%YTVP_CHOICE%"=="2" call "%~dp0START_COLAB_YANDEX.bat" & goto :YT_VIDEO_PRODUCTION_MENU
if "%YTVP_CHOICE%"=="3" call "%~dp0START_COLAB_CHROME.bat" & goto :YT_VIDEO_PRODUCTION_MENU
if "%YTVP_CHOICE%"=="4" call "%~dp0START_COLAB_ALL.bat" & goto :YT_VIDEO_PRODUCTION_MENU
if "%YTVP_CHOICE%"=="5" (
  %PY_CMD% -m orchestrator youtube video queue-status --story-id "Becoming A Slut Wife Alma"
  pause
  goto :YT_VIDEO_PRODUCTION_MENU
)
if "%YTVP_CHOICE%"=="6" (
  %PY_CMD% -m orchestrator youtube video validate-job-assets --story-id "Becoming A Slut Wife Alma"
  pause
  goto :YT_VIDEO_PRODUCTION_MENU
)
if "%YTVP_CHOICE%"=="7" (
  %PY_CMD% -m orchestrator youtube video cleanup-partial-checkpoints --story-id "Becoming A Slut Wife Alma" --dry-run
  echo.
  echo This was dry-run only.
  pause
  goto :YT_VIDEO_PRODUCTION_MENU
)
if "%YTVP_CHOICE%"=="8" (
  %PY_CMD% -m orchestrator youtube video colab-browser-profiles
  pause
  goto :YT_VIDEO_PRODUCTION_MENU
)
goto :YT_VIDEO_PRODUCTION_MENU
:YT_VISUALS_MENU
cls
echo ============================================================
echo   YouTube Visuals
echo ============================================================
echo.
echo [1] Run visuals pipeline for story
echo [2] Visuals status
echo [3] Run frames RunPod only
echo [4] Prepare video segments only
echo [5] Export video job to Google Drive
echo [6] Show video Drive status
echo [7] Import rendered video segments
echo [8] Assemble final video
echo [9] Full video Drive flow
echo [C] Clean visual-state before new characters/prompts
echo [0] Back
echo.
set /p YTV_CHOICE=Choice: 
if "%YTV_CHOICE%"=="0" goto :MAIN_MENU
if "%YTV_CHOICE%"=="1" goto :YT_VISUALS_RUN
if "%YTV_CHOICE%"=="2" goto :YT_VISUALS_STATUS
if "%YTV_CHOICE%"=="3" goto :YT_VISUALS_FRAMES
if "%YTV_CHOICE%"=="4" goto :YT_VISUALS_SEGMENTS
if "%YTV_CHOICE%"=="5" goto :YT_VIDEO_EXPORT_JOB
if "%YTV_CHOICE%"=="6" goto :YT_VIDEO_DRIVE_STATUS
if "%YTV_CHOICE%"=="7" goto :YT_VIDEO_IMPORT_RESULTS
if "%YTV_CHOICE%"=="8" goto :YT_VIDEO_ASSEMBLE_FINAL
if "%YTV_CHOICE%"=="9" goto :YT_VIDEO_FULL_DRIVE_FLOW
if /I "%YTV_CHOICE%"=="C" goto :YT_VISUALS_CLEAN
goto :YT_VISUALS_MENU
:YT_VISUALS_ASK_STORY
set "YTV_STORY="
set /p YTV_STORY=story_id: 
if "%YTV_STORY%"=="" (
  echo [WARN] story_id is required.
  pause
)
goto :eof
:YT_VISUALS_ASK_WORKFLOW
set "YTV_WORKFLOW="
set "YTV_WORKFLOW_ARG="
set /p YTV_WORKFLOW=workflow preset ^(Enter=default from configs\youtube_visuals.yaml^): 
if not "%YTV_WORKFLOW%"=="" set YTV_WORKFLOW_ARG=--workflow "%YTV_WORKFLOW%"
goto :eof
:YT_VISUALS_RUN
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
call :YT_VISUALS_ASK_WORKFLOW
if "%YTV_WORKFLOW%"=="" (
  echo Workflow: default from configs\youtube_visuals.yaml
) else (
  echo Workflow: %YTV_WORKFLOW%
)
echo RunPod URL is NOT needed now.
echo The pipeline will ask for it after Gemini characters/prompts are ready.
echo.
choice /c YN /n /m "Execute? [Y/N]: "
set "YTV_EXEC="
if errorlevel 2 set "YTV_EXEC="
if errorlevel 1 set "YTV_EXEC=--execute"
%PY_CMD% -m orchestrator youtube visuals-run --story-id "%YTV_STORY%" --auto-gemini %YTV_WORKFLOW_ARG% %YTV_EXEC%
pause
goto :YT_VISUALS_MENU
:YT_VISUALS_STATUS
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
%PY_CMD% -m orchestrator youtube visuals-status --story-id "%YTV_STORY%"
pause
goto :YT_VISUALS_MENU
:YT_VISUALS_FRAMES
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
call :YT_VISUALS_ASK_WORKFLOW
set "YTV_RUNPOD="
set /p YTV_RUNPOD=runpod_url ^(required for execute; optional for prepare-only^): 
choice /c YN /n /m "Execute RunPod generation? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator youtube frames-runpod --story-id "%YTV_STORY%" %YTV_WORKFLOW_ARG% --runpod-url "%YTV_RUNPOD%" --prepare-only
) else (
  %PY_CMD% -m orchestrator youtube frames-runpod --story-id "%YTV_STORY%" %YTV_WORKFLOW_ARG% --runpod-url "%YTV_RUNPOD%" --execute
)
pause
goto :YT_VISUALS_MENU
:YT_VISUALS_SEGMENTS
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
set "YTV_SEG=180"
set /p YTV_SEG=segment seconds ^(Enter=180^): 
if "%YTV_SEG%"=="" set "YTV_SEG=180"
choice /c YN /n /m "Write segment manifests? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator youtube video prepare-segments --story-id "%YTV_STORY%" --segment-sec %YTV_SEG%
) else (
  %PY_CMD% -m orchestrator youtube video prepare-segments --story-id "%YTV_STORY%" --segment-sec %YTV_SEG% --execute
)
pause
goto :YT_VISUALS_MENU
:YT_VIDEO_EXPORT_JOB
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
choice /c YN /n /m "Export video job to Google Drive? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator youtube video export-job --story-id "%YTV_STORY%"
) else (
  %PY_CMD% -m orchestrator youtube video export-job --story-id "%YTV_STORY%" --execute
)
pause
goto :YT_VISUALS_MENU
:YT_VIDEO_DRIVE_STATUS
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
%PY_CMD% -m orchestrator youtube video drive-status --story-id "%YTV_STORY%"
pause
goto :YT_VISUALS_MENU
:YT_VIDEO_IMPORT_RESULTS
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
choice /c YN /n /m "Import rendered video segments from Drive? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator youtube video import-results --story-id "%YTV_STORY%"
) else (
  %PY_CMD% -m orchestrator youtube video import-results --story-id "%YTV_STORY%" --execute
)
pause
goto :YT_VISUALS_MENU
:YT_VIDEO_ASSEMBLE_FINAL
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
choice /c YN /n /m "Assemble final_video.mp4 locally? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator youtube video assemble-final --story-id "%YTV_STORY%"
) else (
  %PY_CMD% -m orchestrator youtube video assemble-final --story-id "%YTV_STORY%" --execute
)
pause
goto :YT_VISUALS_MENU
:YT_VIDEO_FULL_DRIVE_FLOW
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
choice /c YN /n /m "Prepare/export Drive video job? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator youtube video full-drive-flow --story-id "%YTV_STORY%"
) else (
  %PY_CMD% -m orchestrator youtube video full-drive-flow --story-id "%YTV_STORY%" --execute
)
pause
goto :YT_VISUALS_MENU
:YT_VISUALS_CLEAN
cls
call :YT_VISUALS_ASK_STORY
if "%YTV_STORY%"=="" goto :YT_VISUALS_MENU
echo.
echo Dry-run cleanup preview:
%PY_CMD% -m orchestrator youtube visuals-clean --story-id "%YTV_STORY%"
echo.
pause
choice /c YN /n /m "Execute cleanup and move files to quarantine? [Y/N]: "
if errorlevel 2 goto :YT_VISUALS_MENU
%PY_CMD% -m orchestrator youtube visuals-clean --story-id "%YTV_STORY%" --execute
pause
goto :YT_VISUALS_MENU
:RUN_SITE_PARTIAL
cls
echo ============================================================
echo   Site: partial run (Phase A / legacy)
echo ============================================================
echo Current Phase A limit: %PHASE_A_LIMIT_LABEL%
if /I "%PHASE_A_LIMIT_MODE%"=="FULL" (
  echo [WARN] Mode is FULL. For a quick test, switch to TEST first.
  choice /c YN /n /m "Switch to TEST and continue? [Y/N]: "
  if errorlevel 2 goto :MAIN_MENU
  > "%PHASE_A_LIMIT_FILE%" echo TEST
  call :INIT_PHASE_A_LIMIT_MODE
)
echo.
echo This runs the legacy partial Site pipeline with the current Phase A limit.
choice /c YN /n /m "Continue? [Y/N]: "
if errorlevel 2 goto :MAIN_MENU
goto :RUN_SITE_PIPELINE
:RUN_SITE_FULL
cls
echo ============================================================
echo   Site FULL: isolated launch mode
echo ============================================================
echo.
echo This is NOT menu [4] Technical stages.
echo.
echo [1] A. Run Site FULL via isolated launch
echo [2] B. Continue existing Site launch
echo [3] C. Status / verify
echo [4] D. Open launch folder
echo [5] E. Open launch logs
echo [6] F. [LEGACY / DANGEROUS] old site run - writes to global runs/output
echo [7] Plan only ^(dry-run, no execute^)
echo [8] G. Monitor launch progress sync loop
echo [9] SITE INTAKE / CREATE SITE LAUNCH
echo [0] Back
echo.
set /p FULL_CHOICE=Choice: 
if "%FULL_CHOICE%"=="0" goto :MAIN_MENU
if "%FULL_CHOICE%"=="1" goto :RUN_SITE_LAUNCH_FULL
if "%FULL_CHOICE%"=="2" goto :RUN_SITE_LAUNCH_RESUME
if "%FULL_CHOICE%"=="3" goto :RUN_SITE_LAUNCH_STATUS
if "%FULL_CHOICE%"=="4" goto :RUN_SITE_LAUNCH_OPEN_FOLDER
if "%FULL_CHOICE%"=="5" goto :RUN_SITE_LAUNCH_OPEN_LOGS
if "%FULL_CHOICE%"=="6" goto :RUN_SITE_PIPELINE_WARN_GLOBAL
if "%FULL_CHOICE%"=="7" goto :RUN_SITE_LAUNCH_PLAN
if "%FULL_CHOICE%"=="8" goto :RUN_SITE_LAUNCH_MONITOR
if "%FULL_CHOICE%"=="9" goto :RUN_SITE_INTAKE_CREATE
goto :RUN_SITE_FULL
:RUN_SITE_INTAKE_CREATE
cls
echo ============================================================
echo   SITE INTAKE / CREATE SITE LAUNCH
echo ============================================================
echo.
set "SITE_INTAKE_SRC=%LIBRARY_SOURCE_DIR%"
set /p SITE_INTAKE_SRC=Library source dir ^(Enter=%LIBRARY_SOURCE_DIR%^): 
if "%SITE_INTAKE_SRC%"=="" set "SITE_INTAKE_SRC=%LIBRARY_SOURCE_DIR%"
if "%SITE_INTAKE_SRC%"=="" (
  echo [ERROR] source dir is empty.
  pause
  goto :RUN_SITE_FULL
)
set "SITE_INTAKE_N="
set /p SITE_INTAKE_N=Stories per top-level folder ^(N^): 
if "%SITE_INTAKE_N%"=="" goto :RUN_SITE_FULL
echo.
echo [DRY-RUN] Plan SITE intake
%PY_CMD% -m orchestrator site intake --source-dir "%SITE_INTAKE_SRC%" --per-folder %SITE_INTAKE_N%
if errorlevel 1 (
  echo [ERROR] site intake dry-run failed.
  pause
  goto :RUN_SITE_FULL
)
echo.
choice /c YN /n /m "Execute create launch and COPY selected stories? [Y/N]: "
if errorlevel 2 goto :RUN_SITE_FULL
%PY_CMD% -m orchestrator site intake --source-dir "%SITE_INTAKE_SRC%" --per-folder %SITE_INTAKE_N% --execute
if errorlevel 1 echo [ERROR] site intake execute failed.
pause
goto :RUN_SITE_FULL
:CHECKS_MENU
cls
echo ============================================================
echo   Checks, reports, logs
echo ============================================================
echo.
echo [1] Preflight ^(non-destructive^)
echo [2] Open reports ^(.orchestrator\reports^)
echo [3] Open logs / status / events
echo [4] Show runtime modes
echo [0] Back
echo.
set /p CHK=Choice: 
if "%CHK%"=="0" goto :MAIN_MENU
if "%CHK%"=="1" goto :PREFLIGHT
if "%CHK%"=="2" goto :OPEN_REPORTS
if "%CHK%"=="3" goto :OPEN_LOGS
if "%CHK%"=="4" goto :SHOW_MODES
goto :CHECKS_MENU
:SERVICE_MENU
cls
echo ============================================================
echo   Service / runtime / cleanup
echo ============================================================
echo.
echo [1] Runtime modes ^(site, TTS, publish, ...^)
echo [2] Toggle Phase A limit ^(TEST / FULL^)
echo [3] Cleanup / quarantine
echo [4] Phase B scaffold ^(NOT production^)
echo [5] Site FULL + Kokoro Google Drive TTS
echo [0] Back
echo.
set /p SVC=Choice: 
if "%SVC%"=="0" goto :MAIN_MENU
if "%SVC%"=="1" goto :MODES_MENU
if "%SVC%"=="2" goto :TOGGLE_PHASE_A_LIMIT_MODE
if "%SVC%"=="3" goto :CLEANUP_MENU
if "%SVC%"=="4" goto :RUN_SCAFFOLD_PHASE_B
if "%SVC%"=="5" goto :RUN_SITE_LAUNCH_FULL
goto :SERVICE_MENU
:YT_STAGES_MENU
cls
echo ============================================================
echo   YouTube: stages
echo ============================================================
echo.
echo Per-stage YouTube runs are not wired in this menu. Use [6] for the full YouTube pipeline.
echo This entry is reserved for future wiring.
echo.
pause
goto :MAIN_MENU
:STORIES_INPUT_MENU
cls
echo ============================================================
echo   Sample library -^> stories\input
echo ============================================================
echo Tool: orchestrator sample-library ^(MOVE; collision-safe by basename in target^).
echo Copies up to N .txt per genre folder from the configured library root into stories\input.
echo Skips _series and other reserved queue names.
echo.
echo [1] Dry-run sampling
echo [2] Move 50 per folder ^(with confirmation^)
echo [3] Custom N per folder
echo [4] Open folder stories\input
echo [0] Back
echo.
set /p SI_CHOICE=Choice: 
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
set "LIB_SRC=%LIBRARY_SOURCE_DIR%"
if "%LIB_SRC%"=="" (
  echo [ERROR] LIBRARY_SOURCE_DIR is empty. Set env LIBRARY_SOURCE_DIR or edit :INIT_LIBRARY_SOURCE_DIR in this .bat
  pause
  goto :STORIES_INPUT_MENU
)
if not exist "%LIB_SRC%" (
  echo [ERROR] Library folder not found: %LIB_SRC%
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
set /p PER_N=Per-folder .txt limit ^(N^): 
if "%PER_N%"=="" goto :STORIES_INPUT_MENU
goto :STORIES_INPUT_WARN_RUN
:STORIES_INPUT_RUN_DRY
call :STORIES_INPUT_ASK_LIB
set "PER_N=50"
cls
echo [DRY-RUN] orchestrator sample-library - no files moved
echo.
%PY_CMD% -m orchestrator sample-library --source-dir "%LIB_SRC%" --target-dir "%~dp0stories\input" --per-folder %PER_N% --dry-run
echo.
echo See stdout ^(skipped_queue_basename, per_folder_planned, manifest_json^).
pause
goto :STORIES_INPUT_MENU
:STORIES_INPUT_WARN_RUN
cls
echo [WARNING] This will MOVE .txt from the library into stories\input ^(sample-library^).
echo Per-folder cap is N; basename collisions in target are skipped.
echo Destructive flags apply ^(allow-nonempty-target, confirm-move, execute^).
echo N=%PER_N%, source: %LIB_SRC%
echo.
choice /c YN /n /m "Continue? [Y/N]: "
if errorlevel 2 goto :STORIES_INPUT_MENU
cls
echo [RUN] orchestrator sample-library - moving files
echo.
%PY_CMD% -m orchestrator sample-library --source-dir "%LIB_SRC%" --target-dir "%~dp0stories\input" --per-folder %PER_N% --allow-nonempty-target --execute --confirm-move
if errorlevel 1 echo [ERROR] sample-library failed. See messages above.
echo.
echo Done. Check stories\input.
pause
goto :STORIES_INPUT_MENU
:PICK_SITE_LAUNCH_NAME
set "LAUNCH_NAME="
echo.
echo === Choose Site launch (no automatic latest-by-mtime) ===
echo Production resume: pick RECOVERY / normal folder, not smoke/test.
set "CHOICE_OUT=%TEMP%\cf_pick_launch_%RANDOM%.setter.cmd"
%PY_CMD% -m orchestrator launch pick-site-launch --out "%CHOICE_OUT%"
if errorlevel 1 (
  echo [WARN] Launch pick cancelled or failed.
  goto :eof
)
if not exist "%CHOICE_OUT%" (
  echo [ERROR] pick-site-launch did not write --out setter .cmd.
  goto :eof
)
call "%CHOICE_OUT%"
del "%CHOICE_OUT%" 2>nul
if "%LAUNCH_NAME%"=="" (
  echo [ERROR] Empty LAUNCH_NAME after pick setter .cmd.
  goto :eof
)
echo [INFO] Selected launch: %LAUNCH_NAME%
goto :eof
:RESOLVE_LAUNCH_PATHS
for /f "delims=" %%I in ('%PY_CMD% -c "from pathlib import Path; import sys; root = Path(sys.argv[1]); name = sys.argv[2]; print((root / \"\u0417\u0430\u043f\u0443\u0441\u043a\u0438\" / name).resolve())" "%~dp0." "%LAUNCH_NAME%"') do set "LAUNCH_ROOT=%%I"
for /f "delims=" %%I in ('%PY_CMD% -c "from pathlib import Path; import sys; print((Path(sys.argv[1]) / \"\u0031\u0030_\u0412\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0435_\u0444\u0430\u0439\u043b\u044b\" / \"legacy\").resolve())" "%LAUNCH_ROOT%"') do set "LAUNCH_LEGACY_ROOT=%%I"
for /f "delims=" %%I in ('%PY_CMD% -c "from pathlib import Path; import sys; print((Path(sys.argv[1]) / \"\u0030\u0037_\u041b\u043e\u0433\u0438\").resolve())" "%LAUNCH_ROOT%"') do set "LAUNCH_LOGS=%%I"
goto :eof
:COUNT_STORIES_TXT
set "FOUND_TXT=0"
for /f %%I in ('dir /b /a-d "%STORIES_DIR%\*.txt" 2^>nul ^| find /c /v ""') do set "FOUND_TXT=%%I"
goto :eof
:PRINT_SITE_LAUNCH_DIAG
call :COUNT_STORIES_TXT
echo [DIAG] stories_dir=%STORIES_DIR%
echo [DIAG] found_txt_files=%FOUND_TXT%
echo [DIAG] launch_name=%LAUNCH_NAME%
echo [DIAG] launch_root=%LAUNCH_ROOT%
echo [DIAG] technical_legacy_root=%LAUNCH_LEGACY_ROOT%
echo [DIAG] phase_a_writes_to=%LAUNCH_LEGACY_ROOT%\runs\site\...
echo [DIAG] site_output_staging=%LAUNCH_LEGACY_ROOT%\output\site\
echo [DIAG] global_runs_site_usage=forbidden
echo [DIAG] global_output_site_usage=forbidden
echo [DIAG] site_tts_engine=kokoro_colab_drive
echo [DIAG] publish_enabled=true
echo [DIAG] youtube_stages_enabled=false
echo [DIAG] launch_mode=%LAUNCH_RESUME_HINT%
goto :eof
:RUN_SITE_LAUNCH_COMMON
set "SITE_FLOW_EXECUTE_FLAG="
if /I "%SITE_FLOW_MODE%"=="execute" set "SITE_FLOW_EXECUTE_FLAG=--execute"
set "SITE_FLOW_LIMIT_ARG=--limit 0"
rem One-shot bypass stuck Drive TTS: empty SKIP_DRIVE_MP3_WAIT.flag next to this bat, or set CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT=1
if exist "%~dp0SKIP_DRIVE_MP3_WAIT.flag" set "CONTENT_FACTORY_SKIP_DRIVE_MP3_WAIT=1"
echo.
echo [CMD] %PY_CMD% -m orchestrator launch run-site-flow --name "%LAUNCH_NAME%" --stories-dir "%STORIES_DIR%" --bat-profile kokoro-drive %SITE_FLOW_LIMIT_ARG% %SITE_FLOW_EXECUTE_FLAG%
%PY_CMD% -m orchestrator launch run-site-flow --name "%LAUNCH_NAME%" --stories-dir "%STORIES_DIR%" --bat-profile kokoro-drive %SITE_FLOW_LIMIT_ARG% %SITE_FLOW_EXECUTE_FLAG%
if errorlevel 1 (
  echo [FAILED] run-site-flow failed (see orchestrator messages above; launch_status should be failed).
  pause
  goto :MAIN_MENU
)
echo.
echo run-site-flow finished for launch=%LAUNCH_NAME% ^(summary line: orchestrator prints [OK]/[FAILED]^)
pause
goto :MAIN_MENU
:RUN_SITE_LAUNCH_FULL
cls
echo [RUN] Site FULL via isolated launch run-site-flow
echo.
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set "LAUNCH_NAME="
for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd_HHmm\")"') do set "AUTO_LAUNCH_TS=%%I"
set "LAUNCH_NAME=SITE_FULL_%AUTO_LAUNCH_TS%"
call :RESOLVE_LAUNCH_PATHS
set "LAUNCH_MANIFEST=%LAUNCH_ROOT%\manifest.json"
set "LAUNCH_RESUME_HINT=new"
if exist "%LAUNCH_MANIFEST%" set "LAUNCH_RESUME_HINT=resume_existing"
echo.
call :PRINT_SITE_LAUNCH_DIAG
echo.
echo Launch name: %LAUNCH_NAME%
echo [INFO] Auto-start: stories\input, launch=%LAUNCH_NAME%
set "SITE_FLOW_MODE=execute"
goto :RUN_SITE_LAUNCH_COMMON
:RUN_SITE_LAUNCH_PLAN
cls
echo [PLAN] Site FULL via isolated launch run-site-flow ^(dry-run^)
echo.
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set "LAUNCH_NAME="
for /f %%I in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMdd_HHmm\")"') do set "AUTO_LAUNCH_TS=%%I"
set "LAUNCH_NAME=SITE_FULL_%AUTO_LAUNCH_TS%"
call :RESOLVE_LAUNCH_PATHS
set "LAUNCH_MANIFEST=%LAUNCH_ROOT%\manifest.json"
set "LAUNCH_RESUME_HINT=new"
if exist "%LAUNCH_MANIFEST%" set "LAUNCH_RESUME_HINT=resume_existing"
echo.
call :PRINT_SITE_LAUNCH_DIAG
echo.
echo Launch name: %LAUNCH_NAME%
echo [INFO] Plan dry-run: stories\input, launch=%LAUNCH_NAME%
set "SITE_FLOW_MODE=plan"
goto :RUN_SITE_LAUNCH_COMMON
:RUN_SITE_LAUNCH_RESUME
cls
echo [RUN] Continue existing Site launch ^(resume^)
echo.
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" (
  echo [WARN] stories_dir is empty ? returning to main menu.
  pause
  goto :MAIN_MENU
)
call :PICK_SITE_LAUNCH_NAME
if "%LAUNCH_NAME%"=="" (
  echo [ERROR] No launch selected. Need ???????/*/manifest.json ^(create via menu [2] item [1] first^).
  pause
  goto :RUN_SITE_FULL
)
echo [INFO] Using launch name: %LAUNCH_NAME% ^(manual pick, not auto-latest^)
echo [INFO] run-site-flow skips phases already done ^(manifest + files on disk^).
call :RESOLVE_LAUNCH_PATHS
set "LAUNCH_MANIFEST=%LAUNCH_ROOT%\manifest.json"
if not exist "%LAUNCH_MANIFEST%" (
  echo [ERROR] launch manifest not found: %LAUNCH_MANIFEST%
  pause
  goto :RUN_SITE_FULL
)
set "LAUNCH_RESUME_HINT=resume_existing"
call :PRINT_SITE_LAUNCH_DIAG
set "SITE_FLOW_MODE=execute"
goto :RUN_SITE_LAUNCH_COMMON
:RUN_SITE_LAUNCH_STATUS
cls
call :PICK_SITE_LAUNCH_NAME
if "%LAUNCH_NAME%"=="" (
  echo [ERROR] No launch selected.
  pause
  goto :RUN_SITE_FULL
)
echo [INFO] Using launch name: %LAUNCH_NAME%
%PY_CMD% -m orchestrator launch verify-runtime --name "%LAUNCH_NAME%"
echo.
%PY_CMD% -m orchestrator launch resume-plan --name "%LAUNCH_NAME%"
pause
goto :RUN_SITE_FULL
:RUN_SITE_LAUNCH_OPEN_FOLDER
cls
call :PICK_SITE_LAUNCH_NAME
if "%LAUNCH_NAME%"=="" (
  echo [ERROR] No launch selected.
  pause
  goto :RUN_SITE_FULL
)
echo [INFO] Using launch name: %LAUNCH_NAME%
call :RESOLVE_LAUNCH_PATHS
if not exist "%LAUNCH_ROOT%" (
  echo [ERROR] launch folder not found: %LAUNCH_ROOT%
  pause
  goto :RUN_SITE_FULL
)
start "" "%LAUNCH_ROOT%"
goto :RUN_SITE_FULL
:RUN_SITE_LAUNCH_OPEN_LOGS
cls
call :PICK_SITE_LAUNCH_NAME
if "%LAUNCH_NAME%"=="" (
  echo [ERROR] No launch selected.
  pause
  goto :RUN_SITE_FULL
)
echo [INFO] Using launch name: %LAUNCH_NAME%
call :RESOLVE_LAUNCH_PATHS
if not exist "%LAUNCH_LOGS%" (
  echo [ERROR] launch logs folder not found: %LAUNCH_LOGS%
  pause
  goto :RUN_SITE_FULL
)
start "" "%LAUNCH_LOGS%"
goto :RUN_SITE_FULL
:RUN_SITE_LAUNCH_MONITOR
cls
echo [MONITOR] launch sync-progress loop
call :PICK_SITE_LAUNCH_NAME
if "%LAUNCH_NAME%"=="" (
  echo [ERROR] No launch selected.
  pause
  goto :RUN_SITE_FULL
)
set "SYNC_INTERVAL_MIN="
set /p SYNC_INTERVAL_MIN=Interval minutes ^(Enter=2^): 
if "%SYNC_INTERVAL_MIN%"=="" set "SYNC_INTERVAL_MIN=2"
set /a SYNC_WAIT_SEC=%SYNC_INTERVAL_MIN%*60
echo.
echo Launch name: %LAUNCH_NAME%
echo Running: python -m orchestrator launch sync-progress --name "%LAUNCH_NAME%"
echo Press Ctrl+C to stop monitor loop.
:RUN_SITE_LAUNCH_MONITOR_LOOP
echo.
echo ===== sync-progress at %DATE% %TIME% =====
%PY_CMD% -m orchestrator launch sync-progress --name "%LAUNCH_NAME%"
echo ===== waiting %SYNC_INTERVAL_MIN% minute^(s^) =====
timeout /t 3 /nobreak >nul
timeout /t %SYNC_WAIT_SEC% /nobreak >nul
goto :RUN_SITE_LAUNCH_MONITOR_LOOP
:RUN_SITE_PIPELINE_WARN_GLOBAL
cls
echo [LEGACY] old site run - writes to global runs/output
echo [WARN] This path writes to runs\site\... and output\site\...
choice /c YN /n /m "Continue legacy run? [Y/N]: "
if errorlevel 2 goto :RUN_SITE_FULL
goto :RUN_SITE_PIPELINE
:RUN_SITE_PIPELINE
cls
echo [RUN] Site: Phase A -^> Phase B -^> orchestrator run site
echo.
set "GEMINI_WORKERS=5"
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set "RUN_ID=site-run"
echo [INFO] run_id=%RUN_ID%
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
echo [INFO] Redirecting to isolated launch mode.
goto :RUN_SITE_LAUNCH_FULL
:RUN_YOUTUBE_PIPELINE
cls
echo [RUN] YouTube: Phase A -^> Phase B -^> orchestrator run youtube
echo.
set "GEMINI_WORKERS=5"
call :ASK_STORIES_DIR
if "%STORIES_DIR%"=="" goto :MAIN_MENU
set "RUN_ID=youtube-run"
echo [INFO] run_id=%RUN_ID%
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
echo [3] Visual prompts: validate / rebuild tables
echo [4] Visual prompts: retry invalid site_info ^(Gemini^)
echo [5] Visual prompts: validate + retry + rebuild ^(full^)
echo [0] back
echo.
set /p STAGE_CHOICE=Choice: 
if "%STAGE_CHOICE%"=="0" goto :MAIN_MENU
if "%STAGE_CHOICE%"=="1" goto :RUN_LENGTH
if "%STAGE_CHOICE%"=="2" goto :RUN_PHASE_B
if "%STAGE_CHOICE%"=="3" goto :RUN_VISUAL_VALIDATE
if "%STAGE_CHOICE%"=="4" goto :RUN_VISUAL_RETRY
if "%STAGE_CHOICE%"=="5" goto :RUN_VISUAL_FULL
goto :STAGE_MENU
:RUN_VISUAL_VALIDATE
cls
echo site-info-visual validate ^(rebuild CSV/XLSX^)
call :ASK_SITE_RUNS_ROOT
if "%SITE_RUNS_ROOT%"=="" goto :STAGE_MENU
%PY_CMD% -m orchestrator site-info-visual validate --runs-root "%SITE_RUNS_ROOT%"
pause
goto :STAGE_MENU
:RUN_VISUAL_RETRY
cls
echo site-info-visual retry ^(только invalid, Gemini site_info_builder^)
call :ASK_SITE_RUNS_ROOT
if "%SITE_RUNS_ROOT%"=="" goto :STAGE_MENU
set "PROFILE_FLAGS=--auto-profile"
choice /c AME /n /m "Выбор Chrome-профиля Gemini: [A]uto-pick free / [M]anual user_data_# / [E]xact default user_data_0 ? "
if errorlevel 3 (
  set "PROFILE_FLAGS="
) else if errorlevel 2 (
  set /p UDX="Введите индекс профиля (0..4): "
  if not "%UDX%"=="" (
    set "PROFILE_FLAGS=--profile %UDX%"
  )
)
choice /c YN /n /m "Запустить Gemini с --execute? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator site-info-visual retry --runs-root "%SITE_RUNS_ROOT%" %PROFILE_FLAGS%
) else (
  %PY_CMD% -m orchestrator site-info-visual retry --runs-root "%SITE_RUNS_ROOT%" --execute %PROFILE_FLAGS%
)
pause
goto :STAGE_MENU
:RUN_VISUAL_FULL
cls
echo site-info-visual full: validate + retry + validate
call :ASK_SITE_RUNS_ROOT
if "%SITE_RUNS_ROOT%"=="" goto :STAGE_MENU
set "PROFILE_FLAGS=--auto-profile"
choice /c AME /n /m "Выбор Chrome-профиля Gemini: [A]uto-pick free / [M]anual user_data_# / [E]xact default user_data_0 ? "
if errorlevel 3 (
  set "PROFILE_FLAGS="
) else if errorlevel 2 (
  set /p UDX="Введите индекс профиля (0..4): "
  if not "%UDX%"=="" (
    set "PROFILE_FLAGS=--profile %UDX%"
  )
)
choice /c YN /n /m "Запустить Gemini retry с --execute? [Y/N]: "
if errorlevel 2 (
  %PY_CMD% -m orchestrator site-info-visual full --runs-root "%SITE_RUNS_ROOT%" %PROFILE_FLAGS%
) else (
  %PY_CMD% -m orchestrator site-info-visual full --runs-root "%SITE_RUNS_ROOT%" --execute %PROFILE_FLAGS%
)
pause
goto :STAGE_MENU
:ASK_SITE_RUNS_ROOT
set "SITE_RUNS_ROOT="
for /f "usebackq delims=" %%I in (`%PY_CMD% -c "from pathlib import Path; from orchestrator.config import load_config; c=load_config(Path('configs/orchestrator.yaml')); roots=sorted((c.root_dir/'Запуски').glob('*/10_Временные_файлы/legacy/runs/site/*-a'), key=lambda p:p.stat().st_mtime, reverse=True); print(roots[0] if roots else '')"`) do set "SITE_RUNS_ROOT=%%I"
if "%SITE_RUNS_ROOT%"=="" (
  echo [WARN] Не найден launch runs/site/*-a. Укажите путь вручную:
  set /p SITE_RUNS_ROOT=runs-root: 
)
if not "%SITE_RUNS_ROOT%"=="" echo Using runs-root: %SITE_RUNS_ROOT%
exit /b 0
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
set "RUN_ID=site-run"
echo [INFO] run_id=%RUN_ID%
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
  echo [WARN] No .txt in stories\input yet. Path is still set; add files or use menu [Q] sample-library.
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
:INIT_LIBRARY_SOURCE_DIR
if defined LIBRARY_SOURCE_DIR goto :eof
set "CF_LIB_TMP=%TEMP%\cf_lib_root_%RANDOM%_%RANDOM%.tmp"
%PY_CMD% -c "import binascii,sys; p=binascii.unhexlify('443ad09fd180d0bed0b5d0bad182d18b20d181d0bed185d1805c417564696f50726f6a6563745c6f7574707574').decode('utf-8'); open(sys.argv[1],'wb').write(p.encode('cp1251'))" "%CF_LIB_TMP%"
set /p LIBRARY_SOURCE_DIR=<"%CF_LIB_TMP%"
del "%CF_LIB_TMP%" 2>nul
goto :eof
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
echo Goodbye.
exit /b 0
