from __future__ import annotations

import os
import sys
import multiprocessing

import uvicorn


if __name__ == "__main__":
    # PyInstaller on Windows may start without a proper stdout (e.g. via
    # `start /B`), causing uvicorn to crash on `sys.stdout.isatty()`.
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
