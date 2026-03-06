#!/usr/bin/env python3
"""Откат имён: выбор папки через диалог, потом поменять имена файлов обратно."""

import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

from revert_names import run_revert

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FOLDER = SCRIPT_DIR / "all_stories"


def main():
    root = tk.Tk()
    root.withdraw()
    start_dir = str(DEFAULT_FOLDER) if DEFAULT_FOLDER.exists() else str(SCRIPT_DIR)
    folder = filedialog.askdirectory(
        title="Выбери КОРНЕВУЮ папку (внутри неё: категории → папки с рассказами)",
        initialdir=start_dir,
    )
    if not folder:
        return
    path = Path(folder)
    result = run_revert(path)
    if result.get("error"):
        messagebox.showerror("Ошибка", result["error"])
        return
    msg = (
        f"Готово.\n\n"
        f"Поменяно имён: {result['swapped']}\n"
        f"Пропущено папок: {result['skipped']}\n"
        f"Ошибок: {result['errors']}"
    )
    messagebox.showinfo("Откат имён", msg)


if __name__ == "__main__":
    main()
