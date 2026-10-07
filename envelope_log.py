"""Watch two envelopes through the ES-8 and log each one as it happens. Read-only: nothing is ever sent.

Patch: envelope A out -> ES-8 input 3, envelope B out -> ES-8 input 4 (change with --env-a / --env-b).
The two raw walks stay on inputs 1 and 2 (--walk-a / --walk-b). The rate voltage the Contour actually got is not
on the ES-8, so it is estimated from the walk with the SISM settings (--scale, --shift) and the clamp (--low, --high).

For each envelope: peak height, rise time (10% of the peak up to the peak), fall time (peak down to 10% again), the
estimated rate voltage and what that means in multiples of the slider time (each volt lower doubles the time).
Stop with Ctrl-C for a summary.

    python envelope_log.py
"""

from __future__ import annotations

import argparse
import queue
import time

import numpy as np
import sounddevice as sd

from es8 import FULL_SCALE_VOLTS, find_es8

STEP_SECONDS = 0.005  # 200 readings a second is plenty for envelopes that last a second or more
START_VOLTS = 0.15    # an envelope has started when the input rises this far above its resting level
END_FRACTION = 0.05   # ... and ended when it is back within 5% of its peak height for HOLD_SECONDS
HOLD_SECONDS = 0.25
MIN_PEAK = 0.3        # ignore blips smaller than this


class Tracker:
    """Follows one envelope input and reports each completed envelope."""

    def __init__(self, name: str):
        self.name = name
        self.base = None
        self.series: list[tuple[float, float]] = []  # (time, volts above rest) while an envelope is running
        self.quiet_since = None
        self.walk_at_start = None

    def feed(self, t: float, volts: float, walk: float):
        if self.base is None:
            self.base = volts
        if not self.series:
            if volts - self.base > START_VOLTS:
                self.series = [(t, volts - self.base)]
                self.walk_at_start = walk
                self.quiet_since = None
            else:
                self.base += 0.01 * (volts - self.base)  # follow slow drift while resting
            return None
        self.series.append((t, volts - self.base))
        peak = max(v for _, v in self.series)
        if volts - self.base < max(0.03, END_FRACTION * peak):
            self.quiet_since = self.quiet_since if self.quiet_since is not None else t
            if t - self.quiet_since >= HOLD_SECONDS:
                return self._finish()
        else:
            self.quiet_since = None
        return None

    def _finish(self):
        series, self.series = self.series, []
        t0 = series[0][0]
        t_peak, peak = max(series, key=lambda p: p[1])
        if peak < MIN_PEAK:
            return None
        tenth = 0.1 * peak
        rise_start = next(t for t, v in series if v >= tenth)
        fall_end = max(t for t, v in series if v >= tenth)
        return {"start": t0, "peak": peak, "rise": t_peak - rise_start, "fall": fall_end - t_peak,
                "walk": self.walk_at_start}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env-a", type=int, default=3, help="ES-8 input with envelope A (default 3)")
    p.add_argument("--env-b", type=int, default=4, help="ES-8 input with envelope B (default 4)")
    p.add_argument("--walk-a", type=int, default=1, help="ES-8 input with raw walk 1 (default 1)")
    p.add_argument("--walk-b", type=int, default=2, help="ES-8 input with raw walk 2 (default 2)")
    p.add_argument("--scale", type=float, default=0.70, help="SISM SCALE on both channels (default 0.70)")
    p.add_argument("--shift", type=float, default=-2.4, help="SISM SHIFT volts on both channels (default -2.4)")
    p.add_argument("--low", type=float, default=-4.0, help="clamp floor in volts (default -4.0)")
    p.add_argument("--high", type=float, default=-0.5, help="clamp ceiling in volts (default -0.5)")
    args = p.parse_args()

    index, info = find_es8()
    rate = int(info["default_samplerate"])
    chunk = int(rate * STEP_SECONDS)
    jacks = [args.env_a, args.env_b, args.walk_a, args.walk_b]
    for j in jacks:
        if not 1 <= j <= 4:
            raise SystemExit(f"ES-8 input {j} doesn't exist (1-4)")
    cols = [j - 1 for j in jacks]
    q: queue.SimpleQueue = queue.SimpleQueue()

    def callback(indata, frames, time_info, status):
        data = indata[:, :4] * FULL_SCALE_VOLTS
        for start in range(0, frames, chunk):
            part = data[start:start + chunk]
            if len(part):
                q.put(part.mean(axis=0))

    trackers = [("A", Tracker("A"), 0, 2), ("B", Tracker("B"), 1, 3)]
    done: dict[str, list[dict]] = {"A": [], "B": []}
    print(f"Watching envelope A on ES-8 input {args.env_a} and B on input {args.env_b} (read only).")
    print(f"Rate voltage is estimated from the walks: walk x {args.scale:g} {args.shift:+.2f} V, "
          f"clamped to {args.low:+.2f}..{args.high:+.2f} V. Ctrl-C to stop.\n")
    started = time.monotonic()
    count = 0
    try:
        with sd.InputStream(device=index, samplerate=rate, channels=4, dtype="float32", callback=callback):
            while True:
                row = q.get()
                t = count * STEP_SECONDS
                count += 1
                vals = [row[c] for c in cols]  # env A, env B, walk A, walk B
                for name, tracker, env_i, walk_i in trackers:
                    found = tracker.feed(t, vals[env_i], vals[walk_i])
                    if found:
                        est = min(args.high, max(args.low, args.scale * found["walk"] + args.shift))
                        found["rate"] = est
                        found["x"] = 2 ** (-est)
                        done[name].append(found)
                        print(f"{time.monotonic() - started:7.1f}s  env {name}  peak {found['peak']:5.2f} V  "
                              f"rise {found['rise']:6.2f}s  fall {found['fall']:6.2f}s  "
                              f"est. rate {est:+.2f} V (~{found['x']:.1f}x slider)", flush=True)
    except KeyboardInterrupt:
        pass

    print("\nSummary:")
    for name in ("A", "B"):
        events = done[name]
        if not events:
            print(f"  env {name}: no envelopes seen")
            continue
        rises = [e["rise"] for e in events]
        falls = [e["fall"] for e in events]
        rates = [e["rate"] for e in events]
        print(f"  env {name}: {len(events)} envelopes, rise {min(rises):.2f}..{max(rises):.2f}s, "
              f"fall {min(falls):.2f}..{max(falls):.2f}s, est. rate {min(rates):+.2f}..{max(rates):+.2f} V")


if __name__ == "__main__":
    main()
