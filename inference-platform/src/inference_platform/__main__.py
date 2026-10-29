"""`python -m inference_platform` / `uv run inference-platform`."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "inference_platform.api:app",
        host=os.getenv("INFERENCE_HOST", "127.0.0.1"),
        port=int(os.getenv("INFERENCE_PORT", "7997")),
        log_level=os.getenv("INFERENCE_LOG_LEVEL", "info").lower(),
        # One worker on purpose: each worker is another copy of every weight in
        # RAM, and the workload is GPU/MPS-bound rather than CPU-parallel.
        workers=1,
    )


if __name__ == "__main__":
    main()
