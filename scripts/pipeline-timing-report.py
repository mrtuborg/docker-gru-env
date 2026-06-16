#!/usr/bin/env python3
"""
pipeline-timing-report.py — per-session tool-call hotspot analysis from OTEL data.

Reads ~/.copilot/otel/copilot-otel.jsonl and reports:
  - Total wall time per session (traceId)
  - Time split: LLM completion vs tool execution
  - Top-N slowest tool calls
  - Any bash.execute calls exceeding a threshold (potential hangs)

Usage:
  python3 scripts/pipeline-timing-report.py [--sessions N] [--hang-threshold 300] [--since YYYY-MM-DD]

Options:
  --sessions N          Show the N most recent sessions (default: 10)
  --hang-threshold S    Flag bash.execute spans longer than S seconds (default: 300)
  --since YYYY-MM-DD    Only show sessions starting on or after this date
  --top N               Show top-N slowest tool calls per session (default: 10)
"""

import argparse
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


OTEL_PATH = Path("~/.copilot/otel/copilot-otel.jsonl").expanduser()
TOOL_PREFIX = "execute_tool "
CHAT_PREFIX = "chat "


def span_duration_s(span: dict) -> float:
    """Convert [sec, nanosec] OTEL time pairs to a duration in seconds."""
    start = span.get("startTime")
    end = span.get("endTime")
    if not start or not end:
        return 0.0
    # Each is [seconds_epoch, nanoseconds]
    start_ns = start[0] * 1_000_000_000 + start[1]
    end_ns = end[0] * 1_000_000_000 + end[1]
    return max(0.0, (end_ns - start_ns) / 1_000_000_000)


def span_start_epoch(span: dict) -> float:
    t = span.get("startTime")
    if not t:
        return 0.0
    return t[0] + t[1] / 1_000_000_000


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sessions", type=int, default=10, metavar="N",
                   help="Number of most-recent sessions to show (default: 10)")
    p.add_argument("--hang-threshold", type=float, default=300.0, metavar="S",
                   help="Flag bash.execute spans longer than S seconds (default: 300)")
    p.add_argument("--since", metavar="YYYY-MM-DD",
                   help="Only show sessions starting on or after this date")
    p.add_argument("--top", type=int, default=10, metavar="N",
                   help="Show top-N slowest tool calls per session (default: 10)")
    return p.parse_args()


def load_spans() -> "list[dict]":
    if not OTEL_PATH.exists():
        print(f"[error] OTEL log not found at {OTEL_PATH}", file=sys.stderr)
        sys.exit(1)
    spans = []
    for line in OTEL_PATH.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            spans.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return spans


def group_by_trace(spans: "list[dict]") -> "dict[str, list[dict]]":
    by_trace = collections.defaultdict(list)
    for s in spans:
        tid = s.get("traceId")
        if tid:
            by_trace[tid].append(s)
    return by_trace


def session_summary(trace_id: str, trace_spans: "list[dict]", args):
    tool_spans = [s for s in trace_spans if s.get("name", "").startswith(TOOL_PREFIX)]
    chat_spans = [s for s in trace_spans if s.get("name", "").startswith(CHAT_PREFIX)]

    if not tool_spans and not chat_spans:
        return None

    all_relevant = tool_spans + chat_spans
    first_start = min(span_start_epoch(s) for s in all_relevant)
    end_times = [
        s["endTime"][0] + s["endTime"][1] / 1_000_000_000
        for s in all_relevant
        if s.get("endTime")
    ]
    last_end = max(end_times) if end_times else first_start
    wall_time_s = max(0.0, last_end - first_start)

    start_dt = datetime.fromtimestamp(first_start, tz=timezone.utc)

    if args.since:
        cutoff = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        if start_dt < cutoff:
            return None

    tool_total_s = sum(span_duration_s(s) for s in tool_spans)
    chat_total_s = sum(span_duration_s(s) for s in chat_spans)

    # Per-tool aggregation
    tool_times: dict[str, list[float]] = collections.defaultdict(list)
    for s in tool_spans:
        name = s["name"][len(TOOL_PREFIX):]
        tool_times[name].append(span_duration_s(s))

    # Model usage
    input_tokens = sum(
        s.get("attributes", {}).get("gen_ai.usage.input_tokens", 0) for s in chat_spans
    )
    output_tokens = sum(
        s.get("attributes", {}).get("gen_ai.usage.output_tokens", 0) for s in chat_spans
    )

    # Hangs
    hangs = [
        (span_duration_s(s), s)
        for s in tool_spans
        if s["name"] == f"{TOOL_PREFIX}bash" and span_duration_s(s) >= args.hang_threshold
    ]

    return {
        "trace_id": trace_id,
        "start_dt": start_dt,
        "wall_time_s": wall_time_s,
        "tool_total_s": tool_total_s,
        "chat_total_s": chat_total_s,
        "tool_count": len(tool_spans),
        "chat_turns": len(chat_spans),
        "tool_times": dict(tool_times),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "hangs": hangs,
    }


def print_bar(label: str, value: float, total: float, width: int = 30) -> str:
    pct = value / total if total > 0 else 0
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{label:<28} {bar} {pct:5.1%}  {value:7.1f}s"


def report_session(s: dict, args):
    dt_str = s["start_dt"].strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'─' * 70}")
    print(f"  Session  {s['trace_id'][:12]}…  started {dt_str}")
    print(f"  Wall time: {s['wall_time_s']:.0f}s  |  "
          f"Tool calls: {s['tool_count']}  |  Chat turns: {s['chat_turns']}  |  "
          f"Tokens: {s['input_tokens']:,} in / {s['output_tokens']:,} out")
    print()

    total_measured = s["tool_total_s"] + s["chat_total_s"]
    if total_measured > 0:
        print("  Time breakdown:")
        print("  " + print_bar("LLM completions", s["chat_total_s"], total_measured))
        print("  " + print_bar("Tool execution", s["tool_total_s"], total_measured))
        print()

    # Top-N slowest tools
    flat = sorted(
        [(name, dur) for name, durs in s["tool_times"].items() for dur in durs],
        key=lambda x: x[1], reverse=True
    )[:args.top]
    if flat:
        print(f"  Top-{args.top} slowest tool calls:")
        for name, dur in flat:
            marker = " ⚠️ SLOW" if name == "bash" and dur >= args.hang_threshold else ""
            print(f"    {dur:7.1f}s  {name}{marker}")
    print()

    # Per-tool totals
    agg = sorted(
        [(name, sum(durs), len(durs)) for name, durs in s["tool_times"].items()],
        key=lambda x: x[1], reverse=True
    )[:args.top]
    if agg:
        print("  Tool totals (cumulative):")
        for name, total, count in agg:
            print(f"    {total:7.1f}s  {name} × {count}")
    print()

    if s["hangs"]:
        print("  ⚠️  POTENTIAL HANGS (bash.execute > "
              f"{args.hang_threshold:.0f}s):")
        for dur, span in s["hangs"]:
            print(f"     {dur:.0f}s  span={span.get('spanId', '?')[:8]}")
        print()


def main():
    args = parse_args()
    print(f"Loading OTEL data from {OTEL_PATH} …", file=sys.stderr)
    spans = load_spans()
    print(f"  {len(spans):,} spans loaded", file=sys.stderr)

    by_trace = group_by_trace(spans)
    summaries = []
    for tid, tspans in by_trace.items():
        s = session_summary(tid, tspans, args)
        if s:
            summaries.append(s)

    summaries.sort(key=lambda x: x["start_dt"], reverse=True)
    summaries = summaries[: args.sessions]

    if not summaries:
        print("No sessions found matching the filter.")
        return

    print(f"\n{'=' * 70}")
    print(f"  PIPELINE TIMING REPORT — {len(summaries)} most recent sessions")
    print(f"{'=' * 70}")

    for s in summaries:
        report_session(s, args)

    # Cross-session summary
    print(f"\n{'=' * 70}")
    print("  CROSS-SESSION SUMMARY")
    print(f"{'=' * 70}")
    total_wall = sum(s["wall_time_s"] for s in summaries)
    total_tools = sum(s["tool_count"] for s in summaries)
    total_tokens_in = sum(s["input_tokens"] for s in summaries)
    total_tokens_out = sum(s["output_tokens"] for s in summaries)
    avg_wall = total_wall / len(summaries) if summaries else 0
    print(f"  Sessions analysed : {len(summaries)}")
    print(f"  Total wall time   : {total_wall/60:.1f} min  (avg {avg_wall/60:.1f} min/session)")
    print(f"  Total tool calls  : {total_tools}  (avg {total_tools/len(summaries):.0f}/session)")
    print(f"  Total tokens      : {total_tokens_in:,} in / {total_tokens_out:,} out")
    hang_sessions = [s for s in summaries if s["hangs"]]
    if hang_sessions:
        print(f"\n  ⚠️  {len(hang_sessions)} session(s) had potential hangs (bash > {args.hang_threshold:.0f}s)")
    print()


if __name__ == "__main__":
    main()
