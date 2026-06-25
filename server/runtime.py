"""Runtime helpers."""
from __future__ import annotations

import os


def server_url() -> str:
    """Public URL of this server (for OAuth callbacks)."""
    return os.environ.get("GRU_SERVER_URL", "http://localhost:9400")
