import multiprocessing


if __name__ == "__main__":
    # Windows: дочерний процесс spawn (torch/kokoro и т.д.) не должен снова выполнять CLI.
    if multiprocessing.current_process().name != "MainProcess":
        raise SystemExit(0)

    from .cli import main

    raise SystemExit(main())
