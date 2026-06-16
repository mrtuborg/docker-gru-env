#!/usr/bin/env python3
"""
test_workflow_config.py — Unit tests for src/workflow_config.py.

Covers:
  - Valid config loads successfully and all required keys are accessible
  - Missing required key(s) cause SystemExit(1) with an informative message
  - Relative paths in config resolve relative to the config file's directory

Usage:
    python3 tests/test_workflow_config.py
    python3 -m pytest tests/test_workflow_config.py -v
"""

from __future__ import annotations

import sys
import textwrap
import tempfile
import os
from pathlib import Path

# Ensure src/ is importable
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import pytest
from workflow_config import WorkflowConfig, REQUIRED_KEYS


MINIMAL_CONFIG = textwrap.dedent("""\
    gh_host: github.com
    data_repo: myorg/cost-data
    pages_repo: myorg/myorg.github.io
    project:
      owner: myorg
      number: 42
""")


def write_config(tmp_path: Path, content: str, filename: str = "config.yml") -> Path:
    p = tmp_path / filename
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Valid config
# ---------------------------------------------------------------------------

class TestValidConfig:
    def test_loads_required_keys(self, tmp_path):
        cfg_path = write_config(tmp_path, MINIMAL_CONFIG)
        cfg = WorkflowConfig.load(cfg_path)

        assert cfg.get("gh_host") == "github.com"
        assert cfg.get("data_repo") == "myorg/cost-data"
        assert cfg.get("pages_repo") == "myorg/myorg.github.io"
        assert cfg.get("project.owner") == "myorg"
        assert cfg.get("project.number") == 42

    def test_optional_keys_accessible(self, tmp_path):
        content = MINIMAL_CONFIG + textwrap.dedent("""\
            watcher:
              enabled: true
              threshold_usd: 5.0
        """)
        cfg_path = write_config(tmp_path, content)
        cfg = WorkflowConfig.load(cfg_path)
        assert cfg.get("watcher.enabled") is True
        assert cfg.get("watcher.threshold_usd") == 5.0

    def test_missing_optional_key_raises_key_error(self, tmp_path):
        cfg_path = write_config(tmp_path, MINIMAL_CONFIG)
        cfg = WorkflowConfig.load(cfg_path)
        with pytest.raises(KeyError):
            cfg.get("watcher.enabled")


# ---------------------------------------------------------------------------
# Missing required keys
# ---------------------------------------------------------------------------

class TestMissingRequiredKeys:
    def test_single_missing_key_exits_1(self, tmp_path, capsys):
        # Remove 'gh_host' from config
        content = textwrap.dedent("""\
            data_repo: myorg/cost-data
            pages_repo: myorg/myorg.github.io
            project:
              owner: myorg
              number: 42
        """)
        cfg_path = write_config(tmp_path, content)
        with pytest.raises(SystemExit) as exc_info:
            WorkflowConfig.load(cfg_path)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "gh_host" in captured.err

    def test_multiple_missing_keys_listed(self, tmp_path, capsys):
        # Config with only gh_host present
        content = "gh_host: github.com\n"
        cfg_path = write_config(tmp_path, content)
        with pytest.raises(SystemExit) as exc_info:
            WorkflowConfig.load(cfg_path)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        # All four missing keys should be listed
        for key in ["data_repo", "pages_repo", "project.owner", "project.number"]:
            assert key in captured.err

    def test_empty_config_exits_1(self, tmp_path, capsys):
        cfg_path = write_config(tmp_path, "")
        with pytest.raises(SystemExit) as exc_info:
            WorkflowConfig.load(cfg_path)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# Relative path resolution
# ---------------------------------------------------------------------------

class TestRelativePathResolution:
    def test_prompt_template_resolved_relative_to_config_dir(self, tmp_path):
        # Place a template file next to config
        template_file = tmp_path / "prompt.txt"
        template_file.write_text("hello")

        content = MINIMAL_CONFIG + "prompt_template: prompt.txt\n"
        cfg_path = write_config(tmp_path, content)
        cfg = WorkflowConfig.load(cfg_path)

        resolved = cfg.get("prompt_template")
        assert Path(resolved).is_absolute()
        assert Path(resolved) == template_file.resolve()

    def test_absolute_path_unchanged(self, tmp_path):
        abs_path = str(tmp_path / "absolute_prompt.txt")
        content = MINIMAL_CONFIG + f"prompt_template: {abs_path}\n"
        cfg_path = write_config(tmp_path, content)
        cfg = WorkflowConfig.load(cfg_path)
        assert cfg.get("prompt_template") == abs_path

    def test_relative_path_in_subdir_config(self, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        content = MINIMAL_CONFIG + "prompt_template: ../prompts/tmpl.txt\n"
        cfg_path = write_config(subdir, content)
        cfg = WorkflowConfig.load(cfg_path)

        resolved = Path(cfg.get("prompt_template"))
        expected = (subdir / ".." / "prompts" / "tmpl.txt").resolve()
        assert resolved == expected


if __name__ == "__main__":
    # Allow running directly without pytest
    import unittest
    # Re-run via pytest for proper output
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=Path(__file__).parent.parent,
    )
    sys.exit(result.returncode)
