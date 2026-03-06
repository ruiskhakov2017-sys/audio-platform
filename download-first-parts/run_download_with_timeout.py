#!/usr/bin/env python3
"""
Запуск докачки с общим таймаутом. Через TOTAL_TIMEOUT минут процесс принудительно убивается.
Чтобы не висеть бесконечно, если что-то зависло до наших таймаутов.
"""
import subprocess
import sys
from pathlib import Path

TOTAL_TIMEOUT_SECONDS = 10 * 60  # 10 минут на всё задание

def main():
    script = Path(__file__).resolve().parent / "download_first_parts.py"
    args = [sys.executable, "-u", str(script)] + sys.argv[1:]
    proc = subprocess.Popen(args)
    try:
        proc.wait(timeout=TOTAL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print("\n\nОбщий таймаут (10 мин). Процесс остановлен.", flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        sys.exit(1)
    sys.exit(proc.returncode or 0)

if __name__ == "__main__":
    main()
