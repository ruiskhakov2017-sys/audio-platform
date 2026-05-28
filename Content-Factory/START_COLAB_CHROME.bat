@echo off
setlocal EnableExtensions
cd /d "D:\Cursor AI\Content-Factory"
set "PY_CMD=py -3"
where py >nul 2>nul || set "PY_CMD=python"
%PY_CMD% tools\colab_launcher\launch_colab_group.py --group chrome --mode prepared-notebook-url --auto-run --sequential
pause
