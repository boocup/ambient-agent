"""Play a saved piece (pieces/*.json) on the Morph 4 levels. NO AI: no model, no API, no network.

It reads the chapters in order and sends each chapter's four levels (0-1 = 0-5 V) from four consecutive ES-8 outputs
(default 1-4, to the Morph 4 level CV inputs A-D), gliding over the chapter's ramp time, then waits out the chapter.
Chapters are never shuffled.

    python play_piece.py --dry-run                         # print the timeline, send nothing
    python play_piece.py --time-scale 0.5                  # real voltage, at half time (how it was first heard)
    python play_piece.py --loop                            # ease back to the opening and repeat until Ctrl-C

Ctrl-C (or the end, without --loop) glides all four outputs back to 0 V, so the voices go silent.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

VOICES = ["env1", "env2", "piano", "drone"]
MAX_LEVEL_VOLTS = 5.0          # +5 V is unity on a Morph 4 level input; never send more
DEFAULT_PIECE = Path(__file__).with_name("pieces") / "quiet-dawn-hosted-claude-2026-10-13.json"


def load_piece(path: Path) -> dict:
    piece = json.loads(path.read_text())
    chapters = piece["chapters"]
    for c in chapters:
        missing = [v for v in VOICES if v not in c["levels"]]
        if missing:
            raise SystemExit(f"chapter {c.get('n')} has no level for {missing}")
    return piece


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("piece", nargs="?", type=Path, default=DEFAULT_PIECE, help="piece file (default: the first saved piece)")
    p.add_argument("--time-scale", type=float, default=1.0, help="multiply chapter and ramp times (0.5 = twice as fast)")
    p.add_argument("--master", type=float, default=1.0, help="multiply every level (0-1.5; never goes above 1.0 per voice)")
    p.add_argument("--first-out", type=int, default=1, help="first of the four ES-8 outputs (default 1: outputs 1-4)")
    p.add_argument("--loop", action="store_true", help="ease back to the first chapter and play again until Ctrl-C")
    p.add_argument("--dry-run", action="store_true", help="print the timeline and send nothing to the ES-8")
    args = p.parse_args()

    piece = load_piece(args.piece)
    chapters = piece["chapters"]
    total = sum(c["minutes"] for c in chapters) * 60 * args.time_scale
    print(f"{piece.get('title', args.piece.name)}: {len(chapters)} chapters, {total / 60:.1f} min at time-scale {args.time_scale}"
          + (" (dry run, nothing is sent)" if args.dry_run else f", ES-8 outputs {args.first_out}-{args.first_out + 3}")
          + ". No AI is used.\n")

    es8 = None
    if not args.dry_run:
        from es8 import ES8
        cvs = tuple(range(args.first_out, args.first_out + len(VOICES)))
        trigger = 8 if 8 not in cvs else 7          # ES8 wants a trigger output; it just stays at 0 V
        es8 = ES8(in_channels=[1], out_channel=trigger, level=0.0, cv_channels=cvs, cv_slew=1.0)

    def go(levels: dict, ramp_seconds: float):
        if es8 is None:
            return
        es8.set_slew(MAX_LEVEL_VOLTS / max(1.0, ramp_seconds * args.time_scale))
        for i, v in enumerate(VOICES):
            es8.set_cv(args.first_out + i, min(1.0, max(0.0, levels[v] * args.master)) * MAX_LEVEL_VOLTS)

    try:
        round_no = 0
        while True:
            round_no += 1
            for c in chapters:
                lv = "  ".join(f"{v} {min(1.0, max(0.0, c['levels'][v] * args.master)):.2f}" for v in VOICES)
                wait = c["minutes"] * 60 * args.time_scale
                print(f"{'[' + str(round_no) + '] ' if args.loop else ''}{c['n']}. {c['name'][:24]:24s} {lv}  | {wait / 60:.1f} min, ramp {c['ramp_seconds'] * args.time_scale:.0f}s", flush=True)
                go(c["levels"], c["ramp_seconds"])
                if not args.dry_run:
                    time.sleep(wait)
            if not args.loop:
                break
            if not args.dry_run:                      # ease back to the opening over a slow ramp, then repeat
                print("   (easing back to the opening)", flush=True)
                go(chapters[0]["levels"], 60)
                time.sleep(60 * args.time_scale)
            else:
                break
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if es8:
            es8.close()
    print("Done." if not args.dry_run else "")


if __name__ == "__main__":
    main()
