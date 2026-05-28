@echo off
setlocal EnableExtensions
cd /d "D:\Cursor AI\Content-Factory"
set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"
%PY_CMD% -m orchestrator youtube video watch-queue --story-id "Becoming A Slut Wife Alma" --execute --poll-seconds 60 --stale-minutes 10 --max-attempts 3 --pending-per-worker 1 --max-total-assigned 50 --max-runtime-minutes 240
pause
