"""Runtime detection helpers."""
from __future__ import annotations

import os
from pathlib import Path


def is_container() -> bool:
    """Detect if running inside a Docker container."""
    if os.environ.get("GRU_IN_CONTAINER"):
        return True
    if Path("/.dockerenv").exists():
        return True
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            return "docker" in content or "containerd" in content
    except (FileNotFoundError, PermissionError):
        pass
    return False


def server_url() -> str:
    """Public URL of this server (for OAuth callbacks)."""
    return os.environ.get("GRU_SERVER_URL", "http://localhost:9400")
