@echo off
setlocal EnableExtensions
cd /d "D:\Cursor AI\Content-Factory"
set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"

echo Starting YouTube video watcher in a separate PowerShell window...
start "YouTube Video Watcher" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath 'D:\Cursor AI\Content-Factory'; %PY_CMD% -m orchestrator youtube video watch-queue --story-id 'Becoming A Slut Wife Alma' --execute --poll-seconds 60 --stale-minutes 10 --max-attempts 3 --pending-per-worker 1 --max-total-assigned 50 --max-runtime-minutes 240"

echo Opening Yandex Colab worker tabs...
%PY_CMD% tools\colab_launcher\launch_colab_group.py --group yandex --mode prepared-notebook-url --auto-run --sequential --wait-after-open-seconds 180 --wait-before-next-worker-seconds 300 --wait-for-run-start-seconds 180

echo Waiting 300 seconds before Chrome group...
timeout /t 300 /nobreak

echo Opening Chrome Colab worker tabs...
%PY_CMD% tools\colab_launcher\launch_colab_group.py --group chrome --mode prepared-notebook-url --auto-run --sequential --wait-after-open-seconds 180 --wait-before-next-worker-seconds 300 --wait-for-run-start-seconds 180

echo.
echo Queue status command:
echo   %PY_CMD% -m orchestrator youtube video doctor --story-id "Becoming A Slut Wife Alma"
echo.
echo Autopilot (reclaim/import/retry/dispatch — run in separate window after Colab Run all):
echo   START_YOUTUBE_VIDEO_AUTOPILOT.bat
echo.
echo Manual fallback: if a profile is logged out, fix that profile manually and rerun this launcher.
pause
