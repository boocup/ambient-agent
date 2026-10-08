"""Log every time a slow two-level voltage changes on one ES-8 input, and how long it stayed at each level.
Read-only: nothing is ever sent to the ES-8.

Made for the fifth-shift test: an A-150 switches between 0 V and about 0.58 V (a fifth, 7 semitones) under control of
a random walk, and this watches the multed output so you don't have to time the changes by hand.

    python level_changes.py                  # ES-8 input 1, switch point 0.29 V
    python level_changes.py --input 2 --threshold 0.3

Stop with Ctrl-C for a summary.
"""

from __future__ import annotations

import argparse
import queue
import time
from datetime import datetime

import numpy as np
import sounddevice as sd

from es8 import FULL_SCALE_VOLTS, find_es8

STEP_SECONDS = 0.01    # 100 readings a second
HYSTERESIS = 0.05      # volts of slack around the switch point, so noise doesn't cause false changes
MIN_STAY = 0.05        # a level must hold this long to count as a change


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=int, default=1, help="ES-8 input to watch (default 1)")
    p.add_argument("--threshold", type=float, default=0.29, help="volts halfway between the two levels (default 0.29)")
    args = p.parse_args()
    if not 1 <= args.input <= 4:
        raise SystemExit("ES-8 input must be 1-4")

    index, info = find_es8()
    rate = int(info["default_samplerate"])
    chunk = int(rate * STEP_SECONDS)
    q: queue.SimpleQueue = queue.SimpleQueue()

    def callback(indata, frames, time_info, status):
        data = indata[:, args.input - 1] * FULL_SCALE_VOLTS
        for start in range(0, frames, chunk):
            part = data[start:start + chunk]
            if len(part):
                q.put(float(part.mean()))

    state = None            # "high" or "low"
    since = 0.0             # seconds when the current level began
    level_sum, level_n = 0.0, 0
    pending, pending_since = None, 0.0
    stays = {"low": [], "high": []}
    volts_seen = {"low": [], "high": []}
    count, changes = 0, 0
    print(f"Watching ES-8 input {args.input} (read only). Switch point {args.threshold:+.2f} V. Ctrl-C to stop.\n")
    try:
        with sd.InputStream(device=index, samplerate=rate, channels=info["max_input_channels"], dtype="float32",
                            callback=callback):
            while True:
                volts = q.get()
                t = count * STEP_SECONDS
                count += 1
                if state is None:
                    state = "high" if volts > args.threshold else "low"
                    since = t
                    print(f"{datetime.now():%H:%M:%S}  start at {volts:+.2f} V ({state})", flush=True)
                level_sum += volts
                level_n += 1
                want = None
                if state == "low" and volts > args.threshold + HYSTERESIS:
                    want = "high"
                elif state == "high" and volts < args.threshold - HYSTERESIS:
                    want = "low"
                if want is None:
                    pending = None
                    continue
                if pending != want:
                    pending, pending_since = want, t
                    continue
                if t - pending_since < MIN_STAY:
                    continue
                # a confirmed change: report how long the old level lasted
                stay = pending_since - since
                old_mean = level_sum / level_n
                stays[state].append(stay)
                volts_seen[state].append(old_mean)
                changes += 1
                print(f"{datetime.now():%H:%M:%S}  change #{changes}: {state} -> {want}   was {state} for "
                      f"{stay:6.1f}s at about {old_mean:+.2f} V", flush=True)
                state, since, pending = want, pending_since, None
                level_sum, level_n = 0.0, 0
    except KeyboardInterrupt:
        pass

    print(f"\nSummary: {changes} changes.")
    for name in ("low", "high"):
        s = stays[name]
        if s:
            print(f"  {name}: {len(s)} stays, {min(s):.1f}s .. {max(s):.1f}s, average {sum(s) / len(s):.1f}s, "
                  f"about {sum(volts_seen[name]) / len(volts_seen[name]):+.2f} V")


if __name__ == "__main__":
    main()
