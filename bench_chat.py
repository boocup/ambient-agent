"""How long this Claude Code chat's own calls to hosted Claude take, from the saved session transcripts. Read-only, free.

Claude Code saves each session as a .jsonl file under ~/.claude/projects/<project>/. Every model reply in it has a timestamp,
a request id and token counts, and each reply's parent entry is the message or tool result that triggered it. So for every
request we can work out:
  first   seconds from the trigger (your message, or a tool finishing) until the first piece of the reply was saved
  total   seconds until the last piece was saved
  out     output tokens
and fit  total = fixed + out x per-token  to separate the fixed part (network, the server reading the conversation) from the
generation part. Only numbers are printed, never the conversation. Use --since to compare before and after a change such as
attaching Ethernet:

    python bench_chat.py                              # every session in this project, by day
    python bench_chat.py --since "2026-10-13 09:00"   # before vs after that local time
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_DIR = Path.home() / ".claude" / "projects" / "-Users-peterbuchhop-vcv-dev-ambient-agent"
MAX_SECONDS = 900   # drop anything longer: an idle gap or an interrupted request, not a normal reply


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()   # to local time


def load_requests(files: list[Path]) -> list[dict]:
    out = []
    for path in files:
        entries = {}
        order = []
        for line in path.open():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("uuid") and d.get("timestamp"):
                entries[d["uuid"]] = d
                order.append(d)
        by_request = defaultdict(list)
        for d in order:
            if d.get("type") == "assistant" and d.get("requestId"):
                by_request[d["requestId"]].append(d)
        for rid, blocks in by_request.items():
            blocks.sort(key=lambda d: d["timestamp"])
            parent = entries.get(blocks[0].get("parentUuid"))
            if not parent or parent.get("type") not in ("user", "attachment"):
                continue   # the trigger is your message, a tool result, or the harness's own entry saved just before the call
            start, first, last = (parse_time(parent["timestamp"]), parse_time(blocks[0]["timestamp"]),
                                  parse_time(blocks[-1]["timestamp"]))
            usage = blocks[0]["message"].get("usage") or {}
            tokens_in = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) + \
                        (usage.get("cache_creation_input_tokens") or 0)
            total = (last - start).total_seconds()
            if not 0 < total <= MAX_SECONDS:
                continue
            out.append({"when": start, "first": (first - start).total_seconds(), "total": total,
                        "out": max((b["message"].get("usage") or {}).get("output_tokens") or 0 for b in blocks),
                        "in": tokens_in, "after_tool": bool(parent.get("toolUseResult") is not None)})
    return sorted(out, key=lambda r: r["when"])


def describe(rows: list[dict]) -> str:
    if len(rows) < 3:
        return f"{len(rows)} requests (too few)"
    total = [r["total"] for r in rows]
    first = [r["first"] for r in rows]
    text = (f"{len(rows):>4} requests | first piece median {statistics.median(first):5.1f} s | "
            f"total median {statistics.median(total):5.1f} s, 90th pct {np.percentile(total, 90):5.1f} s")
    out = np.array([r["out"] for r in rows], float)
    if len(rows) >= 8 and out.std() > 0:
        slope, fixed = np.polyfit(out, total, 1)
        text += f" | fixed {max(fixed, 0):4.1f} s + {slope * 100:4.1f} s per 100 output tokens"
    text += f" | median conversation read {int(statistics.median(r['in'] for r in rows)):,} tokens"
    return text


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", help='local time to split before/after, e.g. "2026-10-13 09:00"')
    p.add_argument("--dir", default=str(PROJECT_DIR), help="folder of session .jsonl files")
    args = p.parse_args()
    files = sorted(Path(args.dir).glob("*.jsonl"))
    if not files:
        raise SystemExit(f"No session files in {args.dir}")
    rows = load_requests(files)
    print(f"{len(rows)} requests from {len(files)} session file(s), {rows[0]['when']:%Y-%m-%d} to {rows[-1]['when']:%Y-%m-%d}")
    print("All:", describe(rows))
    if args.since:
        cut = datetime.fromisoformat(args.since).astimezone()
        print("Before:", describe([r for r in rows if r["when"] < cut]))
        print("After: ", describe([r for r in rows if r["when"] >= cut]))
    else:
        days = defaultdict(list)
        for r in rows:
            days[r["when"].strftime("%Y-%m-%d")].append(r)
        for day, group in sorted(days.items()):
            print(f"  {day}:", describe(group))
    print("(times run from the entry saved just before each call to the last piece of its reply, so they include sending the "
          "conversation, the model reading and writing, and the return trip; tool run times are not included)")


if __name__ == "__main__":
    main()
