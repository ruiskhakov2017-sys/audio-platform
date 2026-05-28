@echo off
setlocal EnableExtensions
cd /d "D:\Cursor AI\Content-Factory"
set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"

echo Starting YouTube video watcher in a separate PowerShell window...
start "YouTube Video Watcher" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath 'D:\Cursor AI\Content-Factory'; %PY_CMD% -m orchestrator youtube video watch-queue --story-id 'Becoming A Slut Wife Alma' --execute --poll-seconds 60 --stale-minutes 10 --max-attempts 3 --pending-per-worker 1 --max-total-assigned 50 --max-runtime-minutes 240"

echo Opening Yandex Colab worker tabs...
%PY_CMD% tools\colab_launcher\launch_colab_group.py --group yandex --mode prepared-notebook-url --auto-run --sequential

echo Opening Chrome Colab worker tabs...
%PY_CMD% tools\colab_launcher\launch_colab_group.py --group chrome --mode prepared-notebook-url --auto-run --sequential

echo.
echo Queue status command:
echo   %PY_CMD% -m orchestrator youtube video queue-status --story-id "Becoming A Slut Wife Alma"
echo.
echo Watch queue-status fields:
echo   global_pending, assigned_pending_total, assigned_processing_total
echo   stale_processing_count, segments_done_count/total_segments
echo   failed/permanent_failed_count, asset_preflight_ok
echo.
echo Manual fallback: if a profile is logged out, fix that profile manually and rerun this launcher.
pause
