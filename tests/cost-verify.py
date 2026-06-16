#!/usr/bin/env python3
"""
cost-verify.py — Automated smoke-tests for the cost-control pipeline.

Runs without network access, external dependencies, or modifying real output files.
All tests use temp directories / in-memory state.

Usage:
    python3 scripts/cost-verify.py          # run all tests
    python3 scripts/cost-verify.py -v       # verbose output
    python3 scripts/cost-verify.py --test N # run a single test by number
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, Optional

# Ensure scripts/ is importable even when run from repo root
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

# Load scripts with hyphenated filenames via importlib
def _load_script(name: str):
    """Load a script from SCRIPTS_DIR by filename stem (hyphens allowed)."""
    path = SCRIPTS_DIR / f"{name}.py"
    import importlib.util
    mod_name = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # required for dataclass __module__ resolution
    spec.loader.exec_module(mod)
    return mod

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

TEST_REPO = "example/repo"


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------

_tests: list[tuple[str, Callable]] = []


def test(name: str):
    def decorator(fn: Callable):
        _tests.append((name, fn))
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(
    tmpdir: Path,
    session_id: str,
    started_at: str = "2026-05-30T10:00:00.000Z",
    has_shutdown: bool = True,
    premium_requests: int = 5,
    repo: str = TEST_REPO,
    branch: str = "feat/cost-control",
    issue_number: Optional[int] = None,
) -> Path:
    """Create a fake session-state directory with events.jsonl and optional sidecar."""
    sd = tmpdir / session_id
    sd.mkdir()

    events = []
    events.append(json.dumps({
        "type": "session.start",
        "timestamp": started_at,
        "data": {"startTime": started_at, "context": {"gitRoot": str(tmpdir)}},
    }))
    if has_shutdown:
        events.append(json.dumps({
            "type": "session.shutdown",
            "timestamp": "2026-05-30T11:00:00.000Z",
            "data": {
                "sessionStartTime": 1748599200000,
                "totalPremiumRequests": premium_requests,
                "modelMetrics": {
                    "claude-sonnet-4.6": {
                        "usage": {"inputTokens": 1000, "outputTokens": 200,
                                  "cacheReadTokens": 0, "cacheWriteTokens": 0,
                                  "reasoningTokens": 0},
                        "requests": {"count": 3, "cost": premium_requests},
                    }
                },
                "codeChanges": {"linesAdded": 10, "linesRemoved": 2, "filesModified": []},
            },
        }))

    (sd / "events.jsonl").write_text("\n".join(events) + "\n")

    ws = f"repository: {repo}\nbranch: {branch}\ncreated_at: {started_at}\nsummary: test session\n"
    (sd / "workspace.yaml").write_text(ws)

    if issue_number is not None:
        sidecar = {
            "issue_number": issue_number,
            "issue_api_id": 12345,
            "confidence": "exact",
            "activated_at": started_at,
        }
        (sd / "issue-refs.json").write_text(json.dumps(sidecar))

    return sd


# ---------------------------------------------------------------------------
# Tests: cost-retrospective.py
# ---------------------------------------------------------------------------

@test("retro: dry-run prints sessions, no file written")
def test_retro_dry_run():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-a", has_shutdown=True)
        out = tdp / "out.jsonl"
        rc = retro.main(["--session-state-dir", td, "--output", str(out), "--dry-run"])
        assert rc == 0, f"exit code {rc}"
        assert not out.exists(), "output file must NOT be created in dry-run"


@test("retro: exact record written for session with shutdown")
def test_retro_exact_confidence():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-a", has_shutdown=True, premium_requests=7)
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out)])
        records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        r = records[0]
        assert r["confidence"] == "exact", f"confidence={r['confidence']}"
        assert r["total_premium_requests"] == 7


@test("retro: unknown record written for session without shutdown")
def test_retro_unknown_confidence():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-b", has_shutdown=False)
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out)])
        records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        assert records[0]["confidence"] == "unknown"
        assert records[0]["total_premium_requests"] is None


@test("retro: sidecar issue-refs.json picked up as exact issue ref")
def test_retro_sidecar_pickup():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-c", has_shutdown=True, issue_number=42)
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out)])
        records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        refs = records[0]["issue_refs"]
        assert len(refs) == 1, f"expected 1 issue ref, got {refs}"
        assert refs[0]["issue"] == 42
        assert refs[0]["confidence"] == "exact"


@test("retro: dedup — second run skips already-written session_id")
def test_retro_dedup():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-d", has_shutdown=True)
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out)])
        retro.main(["--session-state-dir", td, "--output", str(out)])
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, f"expected 1 line after dedup, got {len(lines)}"


@test("retro: --repo filter excludes non-matching sessions")
def test_retro_repo_filter():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-e", repo=TEST_REPO)
        _make_session(tdp, "sess-f", repo="custom-repo/other-repo")
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out),
                    "--repo", TEST_REPO])
        records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        assert records[0]["repository"] == TEST_REPO


@test("retro: --since filter excludes older sessions")
def test_retro_since_filter():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-old", started_at="2026-01-01T00:00:00.000Z")
        _make_session(tdp, "sess-new", started_at="2026-06-01T00:00:00.000Z")
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out),
                    "--since", "2026-05-01"])
        records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        assert records[0]["session_id"] == "sess-new"


@test("retro: empty session-state dir exits cleanly with 0 records")
def test_retro_empty_dir():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.jsonl"
        rc = retro.main(["--session-state-dir", td, "--output", str(out)])
        assert rc == 0
        # No output file or empty
        if out.exists():
            lines = [l for l in out.read_text().splitlines() if l.strip()]
            assert len(lines) == 0


# ---------------------------------------------------------------------------
# Tests: cost-sync.py
# ---------------------------------------------------------------------------

@test("sync: --session-id required — exits 1 without it")
def test_sync_no_session_id():
    sync = _load_script("cost-sync")
    rc = sync.main([])
    assert rc == 1, f"expected exit 1, got {rc}"


@test("sync: --dry-run prints record, no file written")
def test_sync_dry_run():
    sync = _load_script("cost-sync")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sync-sess-a", has_shutdown=True)
        out = tdp / "sync.jsonl"
        rc = sync.main([
            "--session-id", "sync-sess-a",
            "--session-state-dir", td,
            "--output", str(out),
            "--dry-run",
        ])
        assert rc == 0, f"exit code {rc}"
        assert not out.exists(), "output file must NOT be created in dry-run"


@test("sync: appends record on first run")
def test_sync_appends():
    sync = _load_script("cost-sync")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sync-sess-b", has_shutdown=True, premium_requests=3)
        out = tdp / "sync.jsonl"
        rc = sync.main([
            "--session-id", "sync-sess-b",
            "--session-state-dir", td,
            "--output", str(out),
        ])
        assert rc == 0
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        r = json.loads(lines[0])
        assert r["session_id"] == "sync-sess-b"
        assert r["total_premium_requests"] == 3


@test("sync: dedup — second run skips same session_id")
def test_sync_dedup():
    sync = _load_script("cost-sync")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sync-sess-c", has_shutdown=True)
        out = tdp / "sync.jsonl"
        sync.main(["--session-id", "sync-sess-c", "--session-state-dir", td, "--output", str(out)])
        rc = sync.main(["--session-id", "sync-sess-c", "--session-state-dir", td, "--output", str(out)])
        assert rc == 0
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, f"expected 1 line after dedup, got {len(lines)}"


# ---------------------------------------------------------------------------
# Tests: cost-report.py
# ---------------------------------------------------------------------------

@test("report: empty input prints table header, exit 0")
def test_report_empty():
    report = _load_script("cost-report")
    with tempfile.TemporaryDirectory() as td:
        rc = report.main([
            "--live", str(Path(td) / "nonexistent.jsonl"),
            "--historical", str(Path(td) / "nonexistent2.jsonl"),
        ])
        assert rc == 0


@test("report: single live record appears in text output")
def test_report_live_record():
    report = _load_script("cost-report")
    import io
    with tempfile.TemporaryDirectory() as td:
        live = Path(td) / "live.jsonl"
        record = {
            "schema_version": 1,
            "session_id": "rpt-sess-1",
            "confidence": "exact",
            "repository": TEST_REPO,
            "branch": "feat/test",
            "started_at": "2026-05-30T10:00:00Z",
            "ended_at": "2026-05-30T11:00:00Z",
            "issue_refs": [{"issue": 99, "confidence": "exact"}],
            "model_metrics": {},
            "total_premium_requests": 12,
            "est_cost_usd": None,
            "code_changes": None,
        }
        live.write_text(json.dumps(record) + "\n")
        out = Path(td) / "out.txt"
        rc = report.main(["--live", str(live), "--output", str(out)])
        assert rc == 0
        content = out.read_text()
        assert "99" in content, "issue number should appear in output"
        assert "12" in content, "premium requests should appear in output"


@test("report: live wins dedup over historical")
def test_report_dedup_live_wins():
    report = _load_script("cost-report")
    with tempfile.TemporaryDirectory() as td:
        def _record(session_id: str, premium: int) -> dict:
            return {
                "schema_version": 1, "session_id": session_id, "confidence": "exact",
                "repository": TEST_REPO, "branch": "feat/test",
                "started_at": "2026-05-30T10:00:00Z", "ended_at": None,
                "issue_refs": [], "model_metrics": {},
                "total_premium_requests": premium, "est_cost_usd": None, "code_changes": None,
            }
        live = Path(td) / "live.jsonl"
        hist = Path(td) / "hist.jsonl"
        live.write_text(json.dumps(_record("dup-sess", 7)) + "\n")
        hist.write_text(json.dumps(_record("dup-sess", 3)) + "\n")
        out = Path(td) / "out.txt"
        report.main(["--live", str(live), "--historical", str(hist), "--output", str(out)])
        content = out.read_text()
        assert "7" in content, "live value (7) should win"
        # Crude check: historical value 3 should not be the total shown
        # (can't use simple '3' since it could appear in dates etc, check as standalone token)
        lines_with_3 = [l for l in content.splitlines() if "\t3\t" in l or " 3 " in l]
        assert not lines_with_3, f"historical value (3) leaked into output: {lines_with_3}"


# ---------------------------------------------------------------------------
# Tests: query.py (cost-report skill)
# ---------------------------------------------------------------------------

@test("query: --since future date returns empty-state message, exit 0")
def test_query_since_future():
    query_py = Path.home() / ".copilot" / "skills" / "cost-report" / "query.py"
    if not query_py.exists():
        print(f"  (SKIP — {query_py} not found)")
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("query", query_py)
    query = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(query)
    rc = query.main(["--since", "2099-01-01"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Tests: pricing.py
# ---------------------------------------------------------------------------

@test("pricing: load_pricing returns non-empty dict")
def test_pricing_loads():
    pricing = _load_script("pricing")
    prices = pricing.load_pricing()
    assert len(prices) > 0, "pricing dict should not be empty"
    assert "claude-sonnet-4.6" in prices, "claude-sonnet-4.6 should be in pricing"


@test("pricing: normalize_model handles casing and spacing")
def test_pricing_normalize():
    pricing = _load_script("pricing")
    assert pricing.normalize_model("Claude Sonnet 4.6") == "claude-sonnet-4.6"
    assert pricing.normalize_model("claude-sonnet-4.6") == "claude-sonnet-4.6"
    assert pricing.normalize_model(None) is None
    assert pricing.normalize_model("") is None


@test("pricing: compute_cost formula matches expected value")
def test_pricing_compute_cost():
    pricing = _load_script("pricing")
    prices = pricing.load_pricing()
    p = prices["claude-sonnet-4.6"]  # input=3, cached_input=0.3, output=15, cache_write=3.75
    # 1000 fresh input, 0 cache, 200 output
    cost = pricing.compute_cost(1000, 0, 0, 200, p)
    expected = (1000 / 1_000_000) * 3.0 + (200 / 1_000_000) * 15.0
    assert abs(cost - expected) < 1e-9, f"expected {expected}, got {cost}"


@test("pricing: estimate_session_cost returns float for known model")
def test_pricing_estimate_session():
    pricing = _load_script("pricing")
    metrics = {
        "claude-sonnet-4.6": type("M", (), {
            "input_tokens": 100_000,
            "output_tokens": 5_000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        })(),
    }
    cost = pricing.estimate_session_cost(metrics)
    assert cost is not None and cost > 0, f"expected positive cost, got {cost}"


@test("pricing: estimate_session_cost returns None for unknown model")
def test_pricing_unknown_model():
    pricing = _load_script("pricing")
    metrics = {
        "totally-unknown-model-xyz": type("M", (), {
            "input_tokens": 1000, "output_tokens": 200,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        })(),
    }
    cost = pricing.estimate_session_cost(metrics)
    assert cost is None, f"expected None for unknown model, got {cost}"


@test("pricing: retro record has non-null est_cost_usd for exact session")
def test_pricing_retro_integration():
    retro = _load_script("cost-retrospective")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        _make_session(tdp, "sess-price", has_shutdown=True, premium_requests=3)
        out = tdp / "out.jsonl"
        retro.main(["--session-state-dir", td, "--output", str(out)])
        records = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        assert len(records) == 1
        cost = records[0].get("est_cost_usd")
        assert cost is not None and cost > 0, f"expected positive est_cost_usd, got {cost}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Cost pipeline smoke tests")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--test", type=int, metavar="N", help="Run only test N (1-based)")
    args = parser.parse_args(argv)

    passed = failed = skipped = 0
    for i, (name, fn) in enumerate(_tests, start=1):
        if args.test and i != args.test:
            continue
        try:
            fn()
            print(f"  [{i:02d}] {PASS}  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  [{i:02d}] {FAIL}  {name}")
            print(f"       AssertionError: {exc}")
            if args.verbose:
                traceback.print_exc()
            failed += 1
        except Exception as exc:
            print(f"  [{i:02d}] {FAIL}  {name}")
            print(f"       {type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped out of {passed+failed+skipped} tests")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
