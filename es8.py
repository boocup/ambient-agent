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
MAX_CV_VOLTS = 5.0       # hard cap on the slow control voltages the agent may send (set_cv)


def find_es8(name: str = "ES-8"):
    for index, info in enumerate(sd.query_devices()):
        if name.lower() in info["name"].lower() and info["max_input_channels"] > 0 and info["max_output_channels"] > 0:
            return index, info
    available = ", ".join(d["name"] for d in sd.query_devices())
    raise SystemExit(f"No audio device named {name!r} found. Devices: {available}")


class ES8:
    BLOCK = 1024  # ~21 ms at 48 kHz: trigger timing is good to about one block

    def __init__(self, in_channels: list[int], out_channel: int, level: float, dry_run: bool = False,
                 name: str = "ES-8", cv_channels: tuple = (), cv_slew: float = 1.0,
                 max_cv_volts: float = MAX_CV_VOLTS):
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
        for c in cv_channels:
            if not 1 <= c <= n_out or c == out_channel:
                raise SystemExit(f"ES-8 control-voltage output {c} is invalid (1-{n_out}, and not the trigger output)")
        self.cv_channels = list(cv_channels)
        self.max_cv_volts = max_cv_volts
        self._cv_step = cv_slew / FULL_SCALE_VOLTS      # fraction of full scale per second (glide speed)
        self._cv_cur = {c - 1: 0.0 for c in cv_channels}
        self._cv_target = {c - 1: 0.0 for c in cv_channels}
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
            for col, cur in self._cv_cur.items():
                max_move = self._cv_step * frames / self.rate   # glide: never jump, whatever the target
                new = cur + max(-max_move, min(max_move, self._cv_target[col] - cur))
                if (new != cur or cur != 0.0) and not self.dry_run:
                    outdata[:, col] = np.linspace(cur, new, frames, endpoint=False, dtype="float32")
                self._cv_cur[col] = new

    def pulse(self, seconds: float = PULSE_SECONDS):
        """A short trigger on the output channel: `level` for `seconds`, otherwise 0 V."""
        if self.dry_run:
            return
        with self._lock:
            self._pulse_left = int(seconds * self.rate)

    def set_cv(self, channel: int, volts: float) -> float:
        """Aim a slow control voltage output at `volts`. It glides there (never jumps) and is capped at
        +-max_cv_volts. Returns the voltage actually requested after the cap."""
        col = channel - 1
        if col not in self._cv_target:
            raise ValueError(f"ES-8 output {channel} was not opened as a control-voltage output")
        volts = max(-self.max_cv_volts, min(self.max_cv_volts, float(volts)))
        with self._lock:
            self._cv_target[col] = volts / FULL_SCALE_VOLTS
        return volts

    def cv_volts(self, channel: int) -> float:
        """Where a control-voltage output is right now (it may still be gliding)."""
        with self._lock:
            return self._cv_cur[channel - 1] * FULL_SCALE_VOLTS

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
            self._cv_step = 10.0 / FULL_SCALE_VOLTS          # glide every control voltage back to 0 V quickly
            for col in self._cv_target:
                self._cv_target[col] = 0.0
        for _ in range(40):
            with self._lock:
                if all(abs(v) < 1e-6 for v in self._cv_cur.values()):
                    break
            time.sleep(0.05)
        time.sleep(2 * self.BLOCK / self.rate)  # let a block of zeros go out before the stream stops
        self._stream.stop()
        self._stream.close()


class ES8Feedback:
    """Same interface as feedback.FeedbackListener, fed by ES-8 inputs.

    The first input (the follower) drives peak detection and the level summary (scaled to 0-127 so --peak numbers
    and the log lines match the MIDI version). Any further inputs are treated as slow "walks": each is summarized
    as where it sits in its recent range and which way it moved.
    """

    POLL_SECONDS = 0.02
    ACTIVE_BELOW, BUSY_ABOVE = 0.12, 0.40  # follower mean, as a fraction of full scale

    def __init__(self, es8: ES8, peak: int | str | None, debug: bool = False, on_peak=None, min_gap: float = 0.0,
                 rack_hint: bool = True):
        self.es8, self.peak, self.debug, self.on_peak, self.min_gap = es8, peak, debug, on_peak, min_gap
        self.rack_hint = rack_hint
        self.detector = AutoPeakDetector() if peak == "auto" else None
        self.n_walks = len(es8.in_channels) - 1
        self._lock = threading.Lock()
        self._f: list[float] = []
        self._w: list[list[float]] = [[] for _ in range(self.n_walks)]
        self._peaks = 0
        self._last_peak_t = -1e9
        self._walk_history: list[list[tuple[float, float]]] = [[] for _ in range(self.n_walks)]  # (low, high)
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
            for stamp, means in self.es8.drain():
                self._record(stamp - self.t0, float(means[0]), [float(m) for m in means[1:]])
            time.sleep(self.POLL_SECONDS)

    def _record(self, t: float, follower: float, walks: list[float]):
        scaled = self._scaled(follower)
        with self._lock:
            self._f.append(follower)
            for series, value in zip(self._w, walks):
                series.append(value)
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
            walk_text = "".join(f"  in{ch} {v:+.3f}" for ch, v in zip(self.es8.in_channels[1:], walks))
            print(f"  [es8 {t:7.2f}s] follower {follower:+.3f} ({follower * FULL_SCALE_VOLTS:+.1f} V){walk_text}")
        if announce:
            level = self.detector.describe() if self.detector else f">= {self.peak}"
            print(f"  [feedback {t:7.2f}s] PEAK: follower {scaled}/127 ({level})")
            if self.on_peak:
                self.on_peak(scaled)

    def take_window(self) -> Window:
        with self._lock:
            f, self._f = self._f, []
            w, self._w = self._w, [[] for _ in range(self.n_walks)]
            peaks, self._peaks = self._peaks, 0
            marks = self.detector.describe() if self.detector else ""
        glitches, self.es8.glitches = self.es8.glitches, 0
        if not f:
            return Window(0, None, None, None, peaks, marks, "no ES-8 audio blocks received")
        fa = np.asarray(f)
        extra, hint = self._walk_summary(w, float(fa.mean()))
        if glitches:
            extra += f"; {glitches} audio glitch(es)"
        return Window(len(f), self._scaled(fa.min()), self._scaled(fa.max()), self._scaled(fa.mean()) * 1.0,
                      peaks, marks, extra.lstrip("; "), hint if self.rack_hint else "")

    def _walk_summary(self, walks: list[list[float]], follower_mean: float):
        activity = ("quiet" if follower_mean < self.ACTIVE_BELOW
                    else "very active" if follower_mean > self.BUSY_ABOVE else "moderately active")
        parts, known_notes = [], []
        for i, (channel, series) in enumerate(zip(self.es8.in_channels[1:], walks)):
            if not series:
                continue
            wa = np.asarray(series)
            lo, hi, mean = float(wa.min()), float(wa.max()), float(wa.mean())
            history = self._walk_history[i] = (self._walk_history[i] + [(lo, hi)])[-12:]
            r_lo, r_hi = min(a for a, _ in history), max(b for _, b in history)
            span = max(r_hi - r_lo, 1e-6)
            where = "low" if (mean - r_lo) / span < 0.33 else "high" if (mean - r_lo) / span > 0.67 else "middle"
            third = max(1, len(wa) // 3)
            drift = float(wa[-third:].mean() - wa[:third].mean())
            eps = max(0.08 * span, 0.01)  # ignore wiggles smaller than 1% of full scale
            trend = "rising" if drift > eps else "falling" if drift < -eps else "steady"
            known = len(history) >= 3 and span > 0.05
            parts.append(f"walk{channel} {lo:+.2f}..{hi:+.2f} (avg {mean:+.2f}), {trend}"
                         + (f", {where} in its range" if known else ""))
            if known:
                known_notes.append(f"input {channel} is {where} and {trend}")
        walk_text = ""
        if known_notes:
            walk_text = (" Slow random walks in the rack: " + "; ".join(known_notes) + ". Let them gently color the "
                         "music (register, density, dynamics); the first may nudge the melody up when high and down "
                         "when low.")
        hint = (f"Rack feedback from the modular since the last phrase: the music was {activity}"
                f" (envelope follower).{walk_text} If the rack was very active, leave more space; if quiet, be a "
                f"little busier. Treat this as a gentle nudge, not an order.")
        return "; ".join(parts), hint
