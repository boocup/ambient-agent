"""Direct access to an Expert Sleepers ES-8: read CV from its inputs, send trigger pulses out of an output.

The ES-8 shows up to the Mac as a CoreAudio device. Its jacks are DC-coupled, so a steady voltage is just a steady
sample value. Full scale (+-1.0) is taken to be about +-10 V; that is an assumption, so calibrate the pulse level
with a meter or scope (--es8-out CH:LEVEL).

One duplex stream runs for the whole session. Its callback only copies a block average of the chosen inputs and
writes zeros (or the pulse) to the outputs; everything else happens on other threads.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

from feedback import AutoPeakDetector, Window

PULSE_SECONDS = 0.1
FULL_SCALE_VOLTS = 10.0  # assumed: +-1.0 in the audio stream is about +-10 V on the jacks


def find_es8(name: str = "ES-8"):
    for index, info in enumerate(sd.query_devices()):
        if name.lower() in info["name"].lower() and info["max_input_channels"] > 0 and info["max_output_channels"] > 0:
            return index, info
    available = ", ".join(d["name"] for d in sd.query_devices())
    raise SystemExit(f"No audio device named {name!r} found. Devices: {available}")


class ES8:
    BLOCK = 1024  # ~21 ms at 48 kHz: trigger timing is good to about one block

    def __init__(self, in_channels: list[int], out_channel: int, level: float, dry_run: bool = False,
                 name: str = "ES-8"):
        index, info = find_es8(name)
        n_in, n_out = info["max_input_channels"], info["max_output_channels"]
        for c in in_channels:
            if not 1 <= c <= n_in:
                raise SystemExit(f"ES-8 input {c} doesn't exist (1-{n_in})")
        if not 1 <= out_channel <= n_out:
            raise SystemExit(f"ES-8 output {out_channel} doesn't exist (1-{n_out})")
        self.device_name = info["name"]
        self.rate = int(info["default_samplerate"])
        self.in_channels = list(in_channels)
        self.out_channel = out_channel
        self.level = level
        self.dry_run = dry_run
        self.glitches = 0  # PortAudio under/overruns: a sign the Mac is too busy for steady real-time audio
        self._in_cols = [c - 1 for c in in_channels]
        self._out_col = out_channel - 1
        self._blocks: deque = deque(maxlen=5000)  # (monotonic time, per-channel block means)
        self._pulse_left = 0
        self._lock = threading.Lock()
        self._stream = sd.Stream(device=index, samplerate=self.rate, blocksize=self.BLOCK, channels=(n_in, n_out),
                                 dtype="float32", callback=self._callback)
        self._stream.start()

    def _callback(self, indata, outdata, frames, time_info, status):
        outdata.fill(0.0)
        if status:
            self.glitches += 1
        self._blocks.append((time.monotonic(), indata[:, self._in_cols].mean(axis=0)))
        with self._lock:
            n = min(frames, self._pulse_left)
            if n:
                outdata[:n, self._out_col] = self.level
                self._pulse_left -= n

    def pulse(self, seconds: float = PULSE_SECONDS):
        """A short trigger on the output channel: `level` for `seconds`, otherwise 0 V."""
        if self.dry_run:
            return
        with self._lock:
            self._pulse_left = int(seconds * self.rate)

    def drain(self) -> list:
        out = []
        while True:
            try:
                out.append(self._blocks.popleft())
            except IndexError:
                return out

    def close(self):
        with self._lock:
            self._pulse_left = 0
        time.sleep(2 * self.BLOCK / self.rate)  # let a block of zeros go out before the stream stops
        self._stream.stop()
        self._stream.close()


class ES8Feedback:
    """Same interface as feedback.FeedbackListener, fed by ES-8 inputs.

    The follower input drives peak detection and the level summary (scaled to 0-127 so --peak numbers and the
    log lines match the MIDI version). The optional walk input is summarized as where it sits in its recent range
    and which way it moved.
    """

    POLL_SECONDS = 0.02
    ACTIVE_BELOW, BUSY_ABOVE = 0.12, 0.40  # follower mean, as a fraction of full scale

    def __init__(self, es8: ES8, peak: int | str | None, debug: bool = False, on_peak=None, min_gap: float = 0.0,
                 rack_hint: bool = True):
        self.es8, self.peak, self.debug, self.on_peak, self.min_gap = es8, peak, debug, on_peak, min_gap
        self.rack_hint = rack_hint
        self.detector = AutoPeakDetector() if peak == "auto" else None
        self.has_walk = len(es8.in_channels) > 1
        self._lock = threading.Lock()
        self._f: list[float] = []
        self._w: list[float] = []
        self._peaks = 0
        self._last_peak_t = -1e9
        self._walk_history: list[tuple[float, float]] = []  # (low, high) of the walk in recent windows
        self._last_debug = 0.0
        self.t0 = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)

    @staticmethod
    def _scaled(value: float) -> int:
        return int(min(127, max(0, round(value * 127))))

    def _poll(self):
        while not self._stop.is_set():
            blocks = self.es8.drain()
            for stamp, means in blocks:
                self._record(stamp - self.t0, float(means[0]), float(means[1]) if self.has_walk else None)
            time.sleep(self.POLL_SECONDS)

    def _record(self, t: float, follower: float, walk: float | None):
        scaled = self._scaled(follower)
        with self._lock:
            self._f.append(follower)
            if walk is not None:
                self._w.append(walk)
            if self.detector is not None:
                is_peak = self.detector.update(t, scaled)
            else:
                is_peak = self.peak is not None and scaled >= self.peak
            announce = is_peak and self._peaks == 0 and t - self._last_peak_t >= self.min_gap
            if announce:
                self._peaks += 1
                self._last_peak_t = t
        if self.debug and t - self._last_debug >= 0.5:
            self._last_debug = t
            print(f"  [es8 {t:7.2f}s] follower {follower:+.3f} ({follower * FULL_SCALE_VOLTS:+.1f} V)"
                  + (f"  walk {walk:+.3f} ({walk * FULL_SCALE_VOLTS:+.1f} V)" if walk is not None else ""))
        if announce:
            level = self.detector.describe() if self.detector else f">= {self.peak}"
            print(f"  [feedback {t:7.2f}s] PEAK: follower {scaled}/127 ({level})")
            if self.on_peak:
                self.on_peak(scaled)

    def take_window(self) -> Window:
        with self._lock:
            f, self._f = self._f, []
            w, self._w = self._w, []
            peaks, self._peaks = self._peaks, 0
            marks = self.detector.describe() if self.detector else ""
        glitches, self.es8.glitches = self.es8.glitches, 0
        if not f:
            return Window(0, None, None, None, peaks, marks, "no ES-8 audio blocks received")
        fa = np.asarray(f)
        extra, hint = self._walk_summary(w, float(fa.mean()), peaks)
        if glitches:
            extra += f"; {glitches} audio glitch(es)"
        return Window(len(f), self._scaled(fa.min()), self._scaled(fa.max()), self._scaled(fa.mean()) * 1.0,
                      peaks, marks, extra.lstrip("; "), hint if self.rack_hint else "")

    def _walk_summary(self, w: list[float], follower_mean: float, peaks: int):
        activity = ("quiet" if follower_mean < self.ACTIVE_BELOW
                    else "very active" if follower_mean > self.BUSY_ABOVE else "moderately active")
        extra, walk_text = "", ""
        if w:
            wa = np.asarray(w)
            lo, hi, mean = float(wa.min()), float(wa.max()), float(wa.mean())
            self._walk_history = (self._walk_history + [(lo, hi)])[-12:]
            r_lo, r_hi = min(a for a, _ in self._walk_history), max(b for _, b in self._walk_history)
            span = max(r_hi - r_lo, 1e-6)
            pos = (mean - r_lo) / span
            where = "low" if pos < 0.33 else "high" if pos > 0.67 else "middle"
            third = max(1, len(wa) // 3)
            drift = float(wa[-third:].mean() - wa[:third].mean())
            eps = max(0.08 * span, 0.01)  # ignore wiggles smaller than 1% of full scale
            trend = "rising" if drift > eps else "falling" if drift < -eps else "steady"
            known = len(self._walk_history) >= 3 and span > 0.05
            extra = f"walk {lo:+.2f}..{hi:+.2f} (avg {mean:+.2f}), {trend}" + (f", {where} in its range" if known else "")
            if known:
                walk_text = (f" The random walk is {where} in its recent range and {trend}; let a high walk nudge the "
                             f"melody upward and a low walk downward.")
        hint = (f"Rack feedback from the modular since the last phrase: the music was {activity}"
                f" (envelope follower).{walk_text} If the rack was very active, leave more space; if quiet, be a "
                f"little busier. Treat this as a gentle nudge, not an order.")
        return extra, hint
