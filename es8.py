"""Direct access to an Expert Sleepers ES-8: read CV from its inputs, send trigger pulses out of an output.

The ES-8 shows up to the Mac as a CoreAudio device. Its jacks are DC-coupled, so a steady voltage is just a steady
sample value. Full scale (+-1.0) is taken to be about +-10 V; that is an assumption, so calibrate the pulse level
with a meter or scope (--es8-out CH:LEVEL).

One duplex stream runs for the whole session. Its callback only copies a block average of the chosen inputs and
writes zeros (or the pulse) to the outputs; everything else happens on other threads.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

from feedback import AutoPeakDetector, Window
from triggers import TriggerMatcher

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
                 max_cv_volts: float = MAX_CV_VOLTS, expanded: bool = False):
        index, info = find_es8(name)
        n_in, n_out = info["max_input_channels"], info["max_output_channels"]
        if "es-8" in info["name"].lower() and not expanded:
            # macOS reports more channels (12 in, 16 out) than the ES-8 has jacks: 4 inputs, 8 outputs. The rest are
            # ADAT channels, which only carry signal when an expander (ES-6 for inputs, ES-3 for outputs) is connected.
            n_in, n_out = min(n_in, 4), min(n_out, 8)
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
        self._blocks: deque = deque(maxlen=5000)  # (monotonic time, per-channel block means, per-channel block peaks)
        self._pulse_left = 0
        self._lock = threading.Lock()
        self._stream = sd.Stream(device=index, samplerate=self.rate, blocksize=self.BLOCK, channels=(n_in, n_out),
                                 dtype="float32", callback=self._callback)
        self._stream.start()

    def _callback(self, indata, outdata, frames, time_info, status):
        outdata.fill(0.0)
        if status:
            self.glitches += 1
        chosen = indata[:, self._in_cols]
        self._blocks.append((time.monotonic(), chosen.mean(axis=0), chosen.max(axis=0)))
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
    """Same interface as feedback.FeedbackListener, fed by ES-8 inputs. Every input kind is optional:

    follower  an envelope follower of the music: drives peak detection and a level summary (scaled to 0-127 so
              --peak numbers match the MIDI version)
    triggers  {voice name: input}: the trigger that actually fired for each voice (e.g. out of a lockout). Matched
              against the notes the agent sent, so it knows which notes sounded
    walks     slow random walks: each is summarized as where it sits in its recent range and which way it moved
    """

    POLL_SECONDS = 0.02
    ACTIVE_BELOW, BUSY_ABOVE = 0.12, 0.40   # follower mean, as a fraction of full scale
    TRIG_ON, TRIG_OFF = 0.15, 0.05          # a trigger is a block peak above ON (about 1.5 V); re-arms below OFF

    def __init__(self, es8: ES8, peak: int | str | None = None, debug: bool = False, on_peak=None,
                 min_gap: float = 0.0, rack_hint: bool = True, follower: int | None = None,
                 walks: tuple = (), triggers: dict | None = None):
        self.es8, self.peak, self.debug, self.on_peak, self.min_gap = es8, peak, debug, on_peak, min_gap
        self.rack_hint = rack_hint
        self._col = {ch: i for i, ch in enumerate(es8.in_channels)}
        self.follower, self.walks, self.triggers = follower, list(walks), dict(triggers or {})
        self.detector = AutoPeakDetector() if (follower and peak == "auto") else None
        self.matcher = TriggerMatcher(list(self.triggers)) if self.triggers else None
        self._trig_high = {v: False for v in self.triggers}
        self._trig_times: dict[str, list[float]] = {v: [] for v in self.triggers}   # for trigger_stats()
        self._walk_samples = [deque(maxlen=720) for _ in self.walks]   # every 0.5 s, about 6 minutes
        self._walk_last: list[float | None] = [None for _ in self.walks]
        self._last_walk_sample = -1e9
        self._lock = threading.Lock()
        self._blocks = 0
        self._f: list[float] = []
        self._w: list[list[float]] = [[] for _ in self.walks]
        self._peaks = 0
        self._last_peak_t = -1e9
        self._walk_history: list[list[tuple[float, float]]] = [[] for _ in self.walks]  # (low, high)
        self._last_debug = 0.0
        self.t0 = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)

    def note_sent(self, voice: str, t: float):
        """The agent sent a note on this voice at monotonic time t."""
        if self.matcher:
            self.matcher.note_sent(voice, t)

    @staticmethod
    def _scaled(value: float) -> int:
        return int(min(127, max(0, round(value * 127))))

    def _poll(self):
        while not self._stop.is_set():
            for stamp, means, peaks in self.es8.drain():
                self._process_block(stamp, means, peaks)
            time.sleep(self.POLL_SECONDS)

    def _process_block(self, stamp: float, means, peaks):
        """One audio block: `stamp` is when it arrived (monotonic), means/peaks are per chosen input."""
        t = stamp - self.t0
        with self._lock:
            self._blocks += 1
        for voice, channel in self.triggers.items():
            col = self._col[channel]
            high = self._trig_high[voice]
            if not high and peaks[col] >= self.TRIG_ON:
                self._trig_high[voice] = True
                with self._lock:
                    self._trig_times[voice].append(stamp)
                self.matcher.trigger_seen(voice, stamp - 0.5 * self.es8.BLOCK / self.es8.rate)  # mid-block
                if self.debug:
                    print(f"  [es8 {t:7.2f}s] trigger on input {channel} ({voice})")
            elif high and peaks[col] < self.TRIG_OFF:
                self._trig_high[voice] = False
        walks = [float(means[self._col[c]]) for c in self.walks]
        with self._lock:
            for series, value in zip(self._w, walks):
                series.append(value)
            for i, value in enumerate(walks):
                self._walk_last[i] = value
            if t - self._last_walk_sample >= 0.5:
                self._last_walk_sample = t
                for samples, value in zip(self._walk_samples, walks):
                    samples.append(value)
        if self.follower:
            self._record_follower(t, float(means[self._col[self.follower]]), walks)

    def _record_follower(self, t: float, follower: float, walks: list[float]):
        scaled = self._scaled(follower)
        with self._lock:
            self._f.append(follower)
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
            walk_text = "".join(f"  in{ch} {v:+.3f}" for ch, v in zip(self.walks, walks))
            print(f"  [es8 {t:7.2f}s] follower {follower:+.3f} ({follower * FULL_SCALE_VOLTS:+.1f} V){walk_text}")
        if announce:
            level = self.detector.describe() if self.detector else f">= {self.peak}"
            print(f"  [feedback {t:7.2f}s] PEAK: follower {scaled}/127 ({level})")
            if self.on_peak:
                self.on_peak(scaled)

    def trigger_stats(self) -> dict:
        """Per voice since the last call: (triggers seen, median gap in s, smallest gap in s)."""
        with self._lock:
            times, self._trig_times = self._trig_times, {v: [] for v in self.triggers}
        out = {}
        for voice, stamps in times.items():
            gaps = [b - a for a, b in zip(stamps, stamps[1:])]
            out[voice] = (len(stamps), statistics.median(gaps) if gaps else None, min(gaps) if gaps else None)
        return out

    def walk_position(self, index: int, min_samples: int = 20) -> float | None:
        """Where walk number `index` is within its own recent range, 0 to 1 (5th to 95th percentile of the last
        few minutes). None until it has `min_samples` readings (2 per second) and a range worth speaking of (a flat
        signal means nothing)."""
        with self._lock:
            samples, last = sorted(self._walk_samples[index]), self._walk_last[index]
        if len(samples) < min_samples or last is None:
            return None
        lo, hi = samples[int(0.05 * (len(samples) - 1))], samples[int(0.95 * (len(samples) - 1))]
        if hi - lo < 0.02:            # less than 2% of full scale (about 0.2 V)
            return None
        return min(1.0, max(0.0, (last - lo) / (hi - lo)))

    def take_window(self) -> Window:
        with self._lock:
            blocks, self._blocks = self._blocks, 0
            f, self._f = self._f, []
            w, self._w = self._w, [[] for _ in self.walks]
            peaks, self._peaks = self._peaks, 0
            marks = self.detector.describe() if self.detector else ""
        glitches, self.es8.glitches = self.es8.glitches, 0
        if not blocks:
            return Window(0, None, None, None, peaks, marks, "no ES-8 audio blocks received")
        fa = np.asarray(f) if f else None
        extras, hint_parts = [], []
        if self.matcher:
            text, hint = self._trigger_summary()
            extras.append(text)
            hint_parts.append(hint)
        walk_extra, walk_hint = self._walk_summary(w)
        extras.append(walk_extra)
        hint_parts.append(walk_hint)
        if fa is not None:
            hint_parts.insert(0, self._activity_hint(float(fa.mean())))
        if glitches:
            extras.append(f"{glitches} audio glitch(es)")
        hint = " ".join(h for h in hint_parts if h)
        if hint:
            hint = ("Rack feedback from the modular since the last phrase: " + hint + " Treat this as a gentle nudge, "
                    "not an order.")
        return Window(blocks, self._scaled(fa.min()) if fa is not None else None,
                      self._scaled(fa.max()) if fa is not None else None,
                      self._scaled(fa.mean()) * 1.0 if fa is not None else None,
                      peaks, marks, "; ".join(e for e in extras if e), hint if self.rack_hint else "")

    def _activity_hint(self, follower_mean: float) -> str:
        activity = ("quiet" if follower_mean < self.ACTIVE_BELOW
                    else "very active" if follower_mean > self.BUSY_ABOVE else "moderately active")
        return (f"the music was {activity} (envelope follower); if the rack was very active, leave more space, "
                f"if quiet, be a little busier.")

    def _trigger_summary(self) -> tuple[str, str]:
        stats = self.matcher.take(time.monotonic())
        parts, hint_bits, blocked_any = [], [], False
        for voice, st in stats.items():
            if not self.matcher.ever_seen[voice]:
                parts.append(f"{voice}: no trigger ever seen on input {self.triggers[voice]} (patched?)")
                continue
            if not st.sent:
                continue
            lat = f", ~{st.latency_ms:.0f} ms delay" if st.latency_ms is not None else ""
            parts.append(f"{voice}: {st.fired}/{st.sent} notes sounded{lat}" + (f", {st.extra} extra" if st.extra else ""))
            hint_bits.append(f"{voice} {st.fired} of {st.sent}")
            blocked_any |= st.blocked > 0
        hint = ""
        if hint_bits:
            hint = "notes that actually sounded (" + ", ".join(hint_bits) + ")."
            if blocked_any:
                hint += (" The rest were blocked because an envelope was still running; leave more room between "
                         "notes so they can all sound.")
        return "; ".join(parts), hint

    def _walk_summary(self, walks: list[list[float]]) -> tuple[str, str]:
        parts, known_notes = [], []
        for i, (channel, series) in enumerate(zip(self.walks, walks)):
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
        hint = ""
        if known_notes:
            hint = ("Slow random walks in the rack: " + "; ".join(known_notes) + ". Let them gently color the music "
                    "(register, density, dynamics); the first may nudge the melody up when high and down when low.")
        return "; ".join(parts), hint
