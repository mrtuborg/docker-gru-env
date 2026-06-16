#!/usr/bin/env python3
"""
md_kanban.py — minimal parser for Obsidian Kanban plugin markdown boards.

An Obsidian Kanban file looks like:

    ---
    kanban-plugin: board
    ---

    ## Todo

    - [ ] First card
    - [ ] A card with a body
      extra detail on the next (indented) line
      - [ ] a nested checklist item (part of the card, NOT a separate card)

    ## Done

    - [x] Finished card

    %% kanban:settings
    ...
    %%

Cards are **top-level** list items (`- [ ]` open / `- [x]` done) at column 0.
Indented continuation lines (including nested checklists) belong to the card
above them; a blank line ends a card. The trailing `%% kanban:settings %%`
block and YAML frontmatter are ignored.

Subcommands
-----------
    columns --file F
        Print column names, one per line.

    list    --file F --column C [--all]
        Print each card's FULL text (title + body), NUL-separated, so a card may
        span multiple lines. By default only OPEN cards; --all includes done.

    done    --file F --column C --card "TITLE" [--write]
        Mark the open card whose first line equals TITLE as done ([x]).
        Preserves the file's original line endings (only the checkbox changes).
        Prints the new file to stdout, or rewrites in place with --write.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, NamedTuple

_HEADING_RE = re.compile(r"^##\s+(.*?)\s*$")
# Top-level card only: the list marker must start at column 0 (no leading
# whitespace), so indented sub-items are treated as card body, not new cards.
_CARD_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s?(?P<text>.*)$")
_SETTINGS_RE = re.compile(r"^%%\s*kanban:settings")
_EOL_RE = re.compile(r"[\r\n]+$")


class Card(NamedTuple):
    column: str
    start: int      # index of the card's first line
    title: str      # first-line text (after the checkbox)
    text: str       # full card text (title + dedented body lines)
    done: bool


def _strip_eol(line: str) -> str:
    return _EOL_RE.sub("", line)


def _read_raw(path: Path) -> str:
    # newline="" disables universal-newline translation so CRLF is preserved.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _plain_lines(path: Path) -> List[str]:
    return [_strip_eol(l) for l in _read_raw(path).splitlines(keepends=True)]


def _parse(plain: List[str]) -> List[Card]:
    """Parse cards from a list of newline-stripped lines."""
    cards: List[Card] = []
    column = ""
    i, n = 0, len(plain)
    while i < n:
        line = plain[i]
        if _SETTINGS_RE.match(line):
            break
        h = _HEADING_RE.match(line)
        if h:
            column = h.group(1)
            i += 1
            continue
        c = _CARD_RE.match(line)
        if c and column:
            title = c.group("text").rstrip()
            body = [title]
            j = i + 1
            # Collect continuation lines until a blank line, a new top-level
            # card, a heading, or the settings block.
            while j < n:
                nxt = plain[j]
                if nxt.strip() == "":
                    break
                if _HEADING_RE.match(nxt) or _CARD_RE.match(nxt) or _SETTINGS_RE.match(nxt):
                    break
                body.append(nxt.strip())
                j += 1
            cards.append(Card(column, i, title, "\n".join(body),
                              c.group("state").lower() == "x"))
            i = j
            continue
        i += 1
    return cards


def cmd_columns(args) -> int:
    plain = _plain_lines(Path(args.file))
    seen: List[str] = []
    for line in plain:
        if _SETTINGS_RE.match(line):
            break
        h = _HEADING_RE.match(line)
        if h and h.group(1) not in seen:
            seen.append(h.group(1))
    print("\n".join(seen))
    return 0


def cmd_list(args) -> int:
    plain = _plain_lines(Path(args.file))
    for card in _parse(plain):
        if card.column != args.column:
            continue
        if args.all or not card.done:
            # NUL-terminate each card so the runner can read multi-line cards.
            sys.stdout.write(card.text)
            sys.stdout.write("\0")
    return 0


def cmd_done(args) -> int:
    path = Path(args.file)
    raw = _read_raw(path)
    ke = raw.splitlines(keepends=True)          # lines WITH original endings
    plain = [_strip_eol(l) for l in ke]
    target = args.card.strip()

    start = None
    for card in _parse(plain):
        if card.column == args.column and not card.done and card.title == target:
            start = card.start
            break
    if start is None:
        print(f"md_kanban: open card not found in column '{args.column}': {target}",
              file=sys.stderr)
        return 1

    # Edit ONLY the checkbox on the card's first line; preserve its line ending
    # and every other line verbatim (no CRLF normalisation).
    ke[start] = re.sub(r"\[ \]", "[x]", ke[start], count=1)
    new_text = "".join(ke)
    if args.write:
        path.write_text(new_text, encoding="utf-8")
    else:
        sys.stdout.write(new_text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Obsidian Kanban markdown parser")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("columns", help="list column names")
    pc.add_argument("--file", required=True)
    pc.set_defaults(func=cmd_columns)

    pl = sub.add_parser("list", help="list cards in a column (NUL-separated)")
    pl.add_argument("--file", required=True)
    pl.add_argument("--column", required=True)
    pl.add_argument("--all", action="store_true", help="include done cards")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("done", help="mark a card done")
    pd.add_argument("--file", required=True)
    pd.add_argument("--column", required=True)
    pd.add_argument("--card", required=True)
    pd.add_argument("--write", action="store_true", help="rewrite the file in place")
    pd.set_defaults(func=cmd_done)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError:
        print(f"md_kanban: file not found: {args.file}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
