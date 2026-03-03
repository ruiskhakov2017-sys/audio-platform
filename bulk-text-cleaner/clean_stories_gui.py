#!/usr/bin/env python3
"""
Окно с кнопкой «Старт» для массовой очистки текстов.
Отчёт в конце: обработано, пропущено, ошибок.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

import sys
from pathlib import Path

# Папка со скриптом — по умолчанию all_stories рядом
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FOLDER = SCRIPT_DIR / "all_stories"

try:
    from clean_stories import run_clean
except Exception as e:
    import traceback
    msg = traceback.format_exc()
    print(msg)
    (SCRIPT_DIR / "error_log.txt").write_text(msg, encoding="utf-8")
    input("Ошибка при загрузке (см. error_log.txt). Enter для выхода...")
    sys.exit(1)


def run_in_thread(root_dir: Path, result_queue: queue.Queue, progress_queue: queue.Queue):
    def progress_cb(current, total, name):
        progress_queue.put((current, total, name))

    result = run_clean(root_dir, progress_callback=progress_cb)
    result_queue.put(result)


def main():
    result_queue = queue.Queue()
    progress_queue = queue.Queue()

    root = tk.Tk()
    root.title("Очистка текстов рассказов")
    root.minsize(420, 320)
    root.geometry("500x400")

    # Папка
    folder_var = tk.StringVar(value=str(DEFAULT_FOLDER))

    f_top = ttk.Frame(root, padding=10)
    f_top.pack(fill=tk.X)

    ttk.Label(f_top, text="Папка, в которой лежат подпапки с рассказами (.txt):").pack(anchor=tk.W)
    row = ttk.Frame(f_top)
    row.pack(fill=tk.X, pady=(2, 8))
    e = ttk.Entry(row, textvariable=folder_var, width=50)
    e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

    def choose_folder():
        start_dir = folder_var.get().strip()
        if not start_dir or not Path(start_dir).exists():
            start_dir = str(SCRIPT_DIR)
        path = filedialog.askdirectory(title="Выберите папку с подпапками", initialdir=start_dir)
        if path:
            folder_var.set(path)

    ttk.Button(row, text="Обзор…", command=choose_folder).pack(side=tk.LEFT)

    # Старт
    btn_start = ttk.Button(f_top, text="Старт")
    btn_start.pack(pady=4)

    # Прогресс
    progress_label = ttk.Label(f_top, text="Нажмите «Старт» для запуска.")
    progress_label.pack(anchor=tk.W, pady=2)
    progress_bar = ttk.Progressbar(f_top, mode="determinate", length=400)
    progress_bar.pack(fill=tk.X, pady=2)

    # Отчёт
    ttk.Label(f_top, text="Отчёт:").pack(anchor=tk.W, pady=(8, 2))
    report_text = scrolledtext.ScrolledText(root, height=12, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
    report_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    def append_report(msg: str):
        report_text.configure(state=tk.NORMAL)
        report_text.insert(tk.END, msg + "\n")
        report_text.see(tk.END)
        report_text.configure(state=tk.DISABLED)

    def poll():
        try:
            while True:
                current, total, name = progress_queue.get_nowait()
                progress_bar["maximum"] = total
                progress_bar["value"] = current
                progress_label.config(text=f"Обработка: {current} / {total} — {name[:40]}")
        except queue.Empty:
            pass
        root.after(200, poll)

    def on_done():
        try:
            result = result_queue.get_nowait()
        except queue.Empty:
            root.after(100, on_done)
            return

        btn_start.config(state=tk.NORMAL)
        progress_label.config(text="Готово.")
        progress_bar["value"] = progress_bar["maximum"]

        if result.get("error"):
            append_report(f"Ошибка: {result['error']}")
            return

        append_report("——— Отчёт ———")
        append_report(f"Обработано файлов: {result['processed']}")
        append_report(f"Пропущено папок:   {result['skipped']}")
        append_report(f"Ошибок:            {result['errors']}")
        append_report(f"Всего папок:       {result['total']}")
        append_report("")
        if after_id is not None:
            root.after_cancel(after_id)

    after_id = None

    def start():
        path = Path(folder_var.get().strip())
        if not path.exists():
            progress_label.config(text="Папка не найдена.")
            return
        if not path.is_dir():
            progress_label.config(text="Укажите папку, а не файл.")
            return

        report_text.configure(state=tk.NORMAL)
        report_text.delete(1.0, tk.END)
        report_text.configure(state=tk.DISABLED)

        progress_bar["value"] = 0
        progress_bar["maximum"] = 100
        progress_label.config(text="Запуск…")
        btn_start.config(state=tk.DISABLED)

        result_queue.queue.clear()
        while not progress_queue.empty():
            try:
                progress_queue.get_nowait()
            except queue.Empty:
                break

        thread = threading.Thread(target=run_in_thread, args=(path, result_queue, progress_queue), daemon=True)
        thread.start()

        nonlocal after_id
        def check_done():
            if thread.is_alive():
                root.after(300, check_done)
            else:
                on_done()
        after_id = root.after(300, check_done)

    btn_start.config(command=start)
    root.after(200, poll)

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(msg)
        log_path = Path(__file__).resolve().parent / "error_log.txt"
        try:
            log_path.write_text(msg, encoding="utf-8")
            print(f"Ошибка записана в {log_path}")
        except Exception:
            pass
        input("Нажмите Enter для выхода...")
        raise
