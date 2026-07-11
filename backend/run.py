from __future__ import annotations

import os
import multiprocessing

import uvicorn


if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PROPOSAL_TOOL_PORT", "8765")),
        log_level=os.getenv("PROPOSAL_TOOL_LOG_LEVEL", "info"),
        reload=False,
    )
