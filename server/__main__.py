"""
Gru Server entry point.

Usage:
    python -m server [--host HOST] [--port PORT] [--data-dir PATH] [--reload]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gru's Lab Server")
    parser.add_argument("--host",     default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port",     type=int, default=9400, help="Bind port (default: 9400)")
    parser.add_argument("--data-dir", default=None, help="Data directory (default: ~/.gru)")
    parser.add_argument("--reload",   action="store_true", help="Auto-reload on code changes (dev mode)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path.home() / ".gru"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Pass data_dir via environment so create_app can read it in lifespan
    import os
    os.environ["GRU_DATA_DIR"] = str(data_dir)

    uvicorn.run(
        "server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
