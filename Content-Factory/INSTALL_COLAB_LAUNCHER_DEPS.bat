@echo off
setlocal EnableExtensions
cd /d "D:\Cursor AI\Content-Factory"
python -m pip install --upgrade pip
python -m pip install pyautogui pyperclip pillow pygetwindow playwright
python -m playwright install chromium
pause
