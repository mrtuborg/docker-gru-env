#!/usr/bin/env python3
"""
workflow_config.py — Loads and validates .gru/config.yml.

Config file format: YAML with required and optional fields.

Required fields: gh_host, data_repo, project.owner, project.number
Optional fields: pages_repo, watcher.max_issues, watcher.pause_between_sessions,
                 watcher.prompts_dir (path to consumer stage handler directory,
                 relative to config file; files named {Stage}.md override built-ins)

Relative paths are resolved relative to the config file's directory.

CLI usage:
    python3 src/workflow_config.py --config PATH --get KEY
    python3 src/workflow_config.py --get gh_host   # uses default config path
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Run: pip install PyYAML", file=sys.stderr)
    sys.exit(1)

REQUIRED_KEYS: list[str] = [
    "gh_host",
    "data_repo",
    "project.owner",
    "project.number",
]

DEFAULT_CONFIG_PATH = ".gru/config.yml"

# Keys whose values are file paths and should be resolved relative to config dir
RELATIVE_PATH_KEYS: set[str] = {"prompt_template", "watcher.prompts_dir", "working_dir"}

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


def _get_nested(data: dict, dotted_key: str) -> Any:
    """Return value for a dotted key like 'project.owner', or raise KeyError."""
    parts = dotted_key.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_key)
        current = current[part]
    return current


def _resolve_relative_paths(data: dict, config_dir: Path) -> None:
    """Resolve known relative-path fields in-place relative to config_dir."""
    for key in RELATIVE_PATH_KEYS:
        try:
            val = _get_nested(data, key)
        except KeyError:
            continue
        if isinstance(val, str) and not os.path.isabs(val):
            _set_nested(data, key, str((config_dir / val).resolve()))


def _set_nested(data: dict, dotted_key: str, value: Any) -> None:
    """Set a value for a dotted key, creating intermediate dicts if needed."""
    parts = dotted_key.split(".")
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _validate_values(data: dict) -> None:
    """Validate field formats. Exits with code 1 listing all violations."""
    errors: list[str] = []

    gh_host = _get_nested(data, "gh_host")
    if not isinstance(gh_host, str) or not gh_host.strip():
        errors.append("gh_host must be a non-empty string")
    elif gh_host.startswith("https://") or gh_host.startswith("http://"):
        errors.append(f"gh_host must be a hostname only (no scheme prefix), got: {gh_host!r}")

    for key in ("data_repo", "pages_repo"):
        try:
            val = _get_nested(data, key)
        except KeyError:
            continue
        if not isinstance(val, str) or not _OWNER_REPO_RE.match(val):
            errors.append(f"{key} must be in 'owner/repo' format, got: {val!r}")

    try:
        num = _get_nested(data, "project.number")
        if not isinstance(num, int) or num <= 0:
            errors.append(f"project.number must be a positive integer, got: {num!r}")
    except KeyError:
        pass  # already caught as missing required key

    try:
        allowed = _get_nested(data, "allowed_repos")
        if not isinstance(allowed, list) or not allowed:
            errors.append("allowed_repos must be a non-empty list of 'owner/repo' strings")
        else:
            for entry in allowed:
                if not isinstance(entry, str) or not _OWNER_REPO_RE.match(entry):
                    errors.append(f"allowed_repos entry must be in 'owner/repo' format, got: {entry!r}")
    except KeyError:
        pass  # optional — defaults to [data_repo] at runtime

    for key in ("prompt_template", "watcher.prompts_dir", "working_dir"):
        try:
            val = _get_nested(data, key)
        except KeyError:
            continue
        if isinstance(val, str) and os.path.isabs(val) and not os.path.exists(val):
            errors.append(f"{key} path does not exist: {val!r}")

    if errors:
        print("ERROR: config file has invalid values:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)


class WorkflowConfig:
    """Loaded and validated workflow config."""

    def __init__(self, data: dict, config_path: Path) -> None:
        self._data = data
        self._path = config_path

    @classmethod
    def load(cls, config_path: str | Path) -> "WorkflowConfig":
        """Load config from *config_path*, validate required keys, return instance.

        Raises SystemExit(1) if any required key is missing.
        """
        path = Path(config_path)
        with path.open("r") as fh:
            data = yaml.safe_load(fh) or {}

        missing = []
        for key in REQUIRED_KEYS:
            try:
                _get_nested(data, key)
            except KeyError:
                missing.append(key)

        if missing:
            print("ERROR: config file is missing required keys:", file=sys.stderr)
            for k in missing:
                print(f"  - {k}", file=sys.stderr)
            sys.exit(1)

        _validate_values(data)
        _resolve_relative_paths(data, path.parent)
        return cls(data, path)

    def get(self, dotted_key: str) -> Any:
        """Return the value for *dotted_key* (e.g. 'project.owner')."""
        return _get_nested(self._data, dotted_key)

    @property
    def data(self) -> dict:
        return self._data


def _default_config_path() -> Path:
    """Return the default config path, searching from cwd upward."""
    candidate = Path.cwd() / DEFAULT_CONFIG_PATH
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load and query workflow config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"Path to config YAML (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--get",
        metavar="KEY",
        required=True,
        help="Dotted key to retrieve (e.g. gh_host, project.owner)",
    )
    args = parser.parse_args()

    config_path = args.config if args.config else _default_config_path()
    cfg = WorkflowConfig.load(config_path)

    try:
        value = cfg.get(args.get)
    except KeyError:
        print(f"ERROR: key '{args.get}' not found in config", file=sys.stderr)
        sys.exit(1)

    print(value)


if __name__ == "__main__":
    main()
