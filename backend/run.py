from __future__ import annotations

import os
import sys
import multiprocessing
from pathlib import Path

import uvicorn


if __name__ == "__main__":
    # PyInstaller on Windows may start without a proper stdout (e.g. via
    # `start /B`), causing uvicorn to crash on `sys.stdout.isatty()`.
    log_path = os.getenv("PROPOSAL_TOOL_LOG_FILE")
    if log_path:
        try:
            log_file = Path(log_path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            stream = log_file.open("a", encoding="utf-8", buffering=1)
            sys.stdout = stream
            sys.stderr = stream
        except OSError:
            pass
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    multiprocessing.freeze_support()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PROPOSAL_TOOL_PORT", "8765")),
        log_level=os.getenv("PROPOSAL_TOOL_LOG_LEVEL", "info"),
        reload=False,
    )
