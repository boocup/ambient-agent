#!/usr/bin/env python3
"""Measure how a Contour 1 (or any envelope generator) responds to a rate voltage from the ES-8.

Patch:  ES-8 output N  ->  the envelope's Rate CV input
        the envelope's output  ->  a spare ES-8 input (--measure)
For each voltage in the sweep the script glides the rate output there, triggers the envelope with a MIDI note (the
same path the agent uses: note -> MIDI-to-CV gate -> trigger -> envelope), records the envelope on the ES-8 input
and reports its rise and fall times. The result is saved to rate_calibration.json, so the agent can later choose a
voltage range that keeps envelopes between the lengths you want.

Stop the agent first: only one program should drive the ES-8 and the MIDI port.

  python calibrate_rate.py --out 2 --measure 5 --port DIN --channel 8
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import mido

from music import parse_note

CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rate_calibration.json")


def analyze(series: list[tuple[float, float]], base: float, min_amplitude: float = 0.02) -> dict:
    """series: (seconds since the trigger, value) pairs. Returns the envelope's peak and timings, or {} if none.

    rise = from 10% of the peak height up to the peak; fall = from the peak down to 10% again.
    """
    if not series:
        return {}
    t_peak, peak = max(series, key=lambda p: p[1])
    amplitude = peak - base
    if amplitude < min_amplitude:
        return {}
    level = base + 0.1 * amplitude
    t_start = next((t for t, v in series if v >= level), None)
    if t_start is None:
        return {}
    t_end = next((t for t, v in series if t > t_peak and v <= level), None)
    return {
        "peak": peak, "amplitude": amplitude,
        "rise": t_peak - t_start,
        "fall": (t_end - t_peak) if t_end is not None else None,   # None: it hadn't finished when we stopped
    }


def measure_envelope(es8, out_port, midi_channel, note, base_seconds=1.0, timeout=45.0, rise_only=False):
    """Trigger once and record until the envelope has finished (or the timeout)."""
    es8.drain()
    time.sleep(base_seconds)
    baseline = sorted(float(m[0]) for _, m, _p in es8.drain())
    base = baseline[len(baseline) // 2] if baseline else 0.0
    es8.drain()
    t0 = time.monotonic()
    if out_port is not None:
        out_port.send(mido.Message("note_on", channel=midi_channel, note=note, velocity=90))
        time.sleep(0.15)
        out_port.send(mido.Message("note_off", channel=midi_channel, note=note, velocity=0))
    series: list[tuple[float, float]] = []
    below_since = None
    while True:
        for stamp, means, _peaks in es8.drain():
            series.append((stamp - t0, float(means[0])))
        now = time.monotonic() - t0
        result = analyze(series, base)
        if result:
            t_peak = max(series, key=lambda p: p[1])[0]
            if rise_only and now - t_peak > 0.6 and series[-1][1] >= base + 0.95 * result["amplitude"]:
                break                              # it has reached the top and is staying there
            done = series and series[-1][1] <= base + 0.1 * result["amplitude"] and series[-1][0] > t_peak
            below_since = (below_since or now) if done else None
            if below_since is not None and now - below_since > 0.5:
                break
        elif now > 4.0:
            break                                  # nothing happened: no envelope on this input
        if now > timeout:
            break
        time.sleep(0.02)
    return base, series


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=int, required=True, help="ES-8 output that feeds the envelope's Rate CV input")
    p.add_argument("--measure", type=int, required=True, help="ES-8 input that receives the envelope's output")
    p.add_argument("--port", help="MIDI output (e.g. DIN) that triggers the envelope; omit with --dry-run")
    p.add_argument("--channel", type=int, default=8, help="MIDI channel of the voice that triggers this envelope")
    p.add_argument("--note", default="D3", help="note to trigger with")
    p.add_argument("--volts", default="0,1,2,3,4,5", help="rate voltages to try, comma separated (capped at 5 V)")
    p.add_argument("--timeout", type=float, default=45.0, help="give up on an envelope after this many seconds")
    p.add_argument("--rise-only", action="store_true",
                   help="the signal rises and then stays high (or you only care about the rise): stop at the peak")
    p.add_argument("--dry-run", action="store_true", help="send no MIDI and no voltages; just read the input")
    args = p.parse_args()

    from es8 import ES8  # imported late so --help works without sounddevice
    from player import find_port

    volts = [float(v) for v in args.volts.split(",")]
    spare = 8 if args.out != 8 else 7      # an unused output for the (unused) trigger channel; the ES-8 has 8
    es8 = ES8([args.measure], out_channel=spare, level=0.0, dry_run=args.dry_run, cv_channels=(args.out,), cv_slew=2.0)
    out_port = None
    if not args.dry_run:
        if not args.port:
            raise SystemExit("Give --port (e.g. DIN), or use --dry-run.")
        out_port = mido.open_output(find_port(args.port))

    print(f"Rate CV on ES-8 output {args.out}; envelope measured on input {args.measure}; "
          f"triggered by {args.note} on MIDI channel {args.channel}")
    print(f"{'volts':>6} {'peak':>7} {'rise s':>8} {'fall s':>8} {'total s':>8}")
    points = []
    try:
        for v in volts:
            sent = es8.set_cv(args.out, v) if not args.dry_run else v
            deadline = time.monotonic() + 8
            while not args.dry_run and abs(es8.cv_volts(args.out) - sent) > 0.02 and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.5)
            base, series = measure_envelope(es8, out_port, args.channel - 1, parse_note(args.note), timeout=args.timeout,
                                          rise_only=args.rise_only)
            r = analyze(series, base)
            if not r:
                print(f"{sent:6.1f}   no envelope seen on input {args.measure} (is its output patched there?)")
                points.append({"volts": sent})
                continue
            total = r["rise"] + r["fall"] if r["fall"] is not None else None
            fall = (f"{r['fall']:8.2f}" if r["fall"] is not None
                    else f"{'-':>8}" if args.rise_only else f">{args.timeout:7.0f}")
            print(f"{sent:6.1f} {r['peak'] * 10:6.1f}V {r['rise']:8.2f} {fall} "
                  + (f"{total:8.2f}" if total is not None else f"{'>':>8}"))
            points.append({"volts": sent, "peak_volts": r["peak"] * 10, "rise": r["rise"], "fall": r["fall"], "total": total})
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if out_port is not None:
            out_port.send(mido.Message("control_change", channel=args.channel - 1, control=123, value=0))
            out_port.close()
        es8.close()                                    # glides the rate output back to 0 V
    if not args.dry_run and any("rise" in pt for pt in points):
        data = json.load(open(CAL_FILE)) if os.path.exists(CAL_FILE) else {}
        data[f"out{args.out}"] = {"measure_in": args.measure, "midi_channel": args.channel, "points": points,
                                  "measured": time.strftime("%Y-%m-%d %H:%M")}
        json.dump(data, open(CAL_FILE, "w"), indent=2)
        print(f"Saved to {CAL_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
