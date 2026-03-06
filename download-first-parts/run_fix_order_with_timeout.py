#!/usr/bin/env python3
"""
Запуск починки порядка глав с общим таймаутом. Через TOTAL_TIMEOUT минут процесс убивается.
"""
import subprocess
import sys
from pathlib import Path

TOTAL_TIMEOUT_SECONDS = 15 * 60  # 15 минут на всё

def main():
    script = Path(__file__).resolve().parent / "fix_first_chapter_order.py"
    if len(sys.argv) < 2:
        print("Usage: python run_fix_order_with_timeout.py <folder>")
        return
    args = [sys.executable, "-u", str(script)] + sys.argv[1:]
    proc = subprocess.Popen(args)
    try:
        proc.wait(timeout=TOTAL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print("\n\nОбщий таймаут (15 мин). Процесс остановлен.", flush=True)
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
