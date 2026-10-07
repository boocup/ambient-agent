"""Watch the voltages arriving at the ES-8 inputs. Read-only: nothing is ever sent to the ES-8.

Prints one line per interval with each input's mean voltage and the lowest and highest it reached, and flags any
input listed in --clamped that leaves the --low/--high range (a small --margin allows for ES-8 scaling error).
Stop with Ctrl-C for a summary of the whole run.

    python monitor_volts.py                      # all 4 inputs every second, inputs 3,4 checked against -4.0..-0.5 V
    python monitor_volts.py --clamped 1,2 --every 0.5

The ES-8 reads as +-1.0 = about +-10 V; that is an assumption (see es8.py), so treat readings as good to a few percent
until checked against a known voltage.
"""

from __future__ import annotations

import argparse
import threading
import time

import numpy as np
import sounddevice as sd

from es8 import FULL_SCALE_VOLTS, find_es8

NAMES = ["in1", "in2", "out1", "out2"]  # ES-8 jacks 1-4: the raw walks, then the clamped MetaModule outputs
JACKS = 4  # the ES-8 has 4 input jacks; macOS lists more (ADAT) channels that carry nothing without an expander


def parse_inputs(text: str) -> list[int]:
    return [int(part) for part in text.split(",") if part.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--every", type=float, default=1.0, help="seconds between printed lines (default 1)")
    parser.add_argument("--clamped", default="3,4", help="inputs expected to stay in range, e.g. 3,4 (0 = none)")
    parser.add_argument("--low", type=float, default=-4.0, help="lowest allowed volts (default -4.0)")
    parser.add_argument("--high", type=float, default=-0.5, help="highest allowed volts (default -0.5)")
    parser.add_argument("--margin", type=float, default=0.15, help="volts of slack before flagging (default 0.15)")
    args = parser.parse_args()

    checked = [c for c in parse_inputs(args.clamped) if c != 0]
    for c in checked:
        if not 1 <= c <= JACKS:
            raise SystemExit(f"Input {c} doesn't exist (1-{JACKS})")

    index, info = find_es8()
    rate = int(info["default_samplerate"])
    n_in = min(info["max_input_channels"], JACKS)

    lock = threading.Lock()
    total = np.zeros(n_in)
    count = 0
    lo = np.full(n_in, np.inf)
    hi = np.full(n_in, -np.inf)
    all_lo = np.full(n_in, np.inf)
    all_hi = np.full(n_in, -np.inf)
    out_of_range = {c: 0 for c in checked}
    at_ceiling = {c: 0 for c in checked}  # intervals spent pinned at the high limit
    at_floor = {c: 0 for c in checked}    # ... and at the low limit
    intervals = 0

    def callback(indata, frames, time_info, status):
        nonlocal total, count, lo, hi
        volts = indata[:, :n_in] * FULL_SCALE_VOLTS
        with lock:
            total += volts.sum(axis=0)
            count += frames
            np.minimum(lo, volts.min(axis=0), out=lo)
            np.maximum(hi, volts.max(axis=0), out=hi)

    print(f"Reading {info['name']} inputs 1-{n_in} (read only). Checking inputs {checked or 'none'} against "
          f"{args.low:+.2f} to {args.high:+.2f} V (+-{args.margin:.2f}). Ctrl-C to stop.\n")
    started = time.monotonic()
    try:
        # Input-only stream: opens no outputs, so it cannot change anything on the rack.
        with sd.InputStream(device=index, samplerate=rate, channels=n_in, dtype="float32", callback=callback):
            while True:
                time.sleep(args.every)
                with lock:
                    if not count:
                        continue
                    mean, line_lo, line_hi = total / count, lo.copy(), hi.copy()
                    total[:] = 0
                    count = 0
                    lo[:] = np.inf
                    hi[:] = -np.inf
                np.minimum(all_lo, line_lo, out=all_lo)
                np.maximum(all_hi, line_hi, out=all_hi)
                intervals += 1
                cells, flags, silent = [], [], []
                for i in range(n_in):
                    cells.append(f"{NAMES[i]} {mean[i]:+6.2f} ({line_lo[i]:+5.2f}..{line_hi[i]:+5.2f})")
                    if (i + 1) in out_of_range:
                        if mean[i] >= args.high - 0.03:
                            at_ceiling[i + 1] += 1
                        elif mean[i] <= args.low + 0.03:
                            at_floor[i + 1] += 1
                        if abs(mean[i]) < 0.03 and line_hi[i] - line_lo[i] < 0.03:
                            silent.append(NAMES[i])  # flat 0 V: almost certainly nothing patched
                        elif line_lo[i] < args.low - args.margin or line_hi[i] > args.high + args.margin:
                            out_of_range[i + 1] += 1
                            flags.append(NAMES[i])
                note = f"  OUT OF RANGE: {', '.join(flags)}" if flags else ""
                note += f"  no signal on {', '.join(silent)} (patched?)" if silent else ""
                print(f"{time.monotonic() - started:7.1f}s  " + "   ".join(cells) + note, flush=True)
    except KeyboardInterrupt:
        pass

    print("\nWhole run, lowest..highest volts per input:")
    for i in range(n_in):
        if np.isfinite(all_lo[i]):
            print(f"  {NAMES[i]}: {all_lo[i]:+.2f} .. {all_hi[i]:+.2f}")
    for c, n in out_of_range.items():
        print(f"  {NAMES[c - 1]}: {'stayed in range' if not n else f'left the range in {n} interval(s)'}")
    if intervals:
        print(f"\nTime pinned at a limit, out of {intervals} intervals of {args.every:g}s:")
        for c in out_of_range:
            print(f"  {NAMES[c - 1]}: {100 * at_ceiling[c] / intervals:.0f}% at the ceiling ({args.high:+.2f} V), "
                  f"{100 * at_floor[c] / intervals:.0f}% at the floor ({args.low:+.2f} V)")


if __name__ == "__main__":
    main()
