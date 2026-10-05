"""Feedback from the rack: listens for one MIDI CC and summarizes it per phrase."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

import rtmidi


@dataclass
class Window:
    """What the CC did during one phrase."""
    count: int
    low: int | None
    high: int | None
    average: float | None
    peaks: int = 0
    marks: str = ""  # how the peak level was set, for the log line
    extra: str = ""  # anything else worth logging (e.g. the ES-8 walk input)
    hint: str = ""   # a sentence for the model about what the rack did (see es8.ES8Feedback)

    def describe(self) -> str:
        if not self.count:
            return "no CC received" if not self.extra else self.extra
        text = f"{self.count} msgs, min {self.low}, avg {self.average:.0f}, max {self.high}, peaks {self.peaks}"
        text += f" ({self.marks})" if self.marks else ""
        return text + (f"; {self.extra}" if self.extra else "")


class AutoPeakDetector:
    """Fires when a CC climbs near the top of its own recent range.

    A fixed threshold fires constantly if the envelope sits high (the Hapax
    maps 0 V to about 64, so a hot follower idles at 118-125). This looks at
    the last `window_s` seconds instead: the range between the 5th and 95th
    percentile of the signal. It fires when the value reaches `high` of the
    way up that range, then re-arms only after the value falls back below
    `low` of the way up, so one swell is one peak. Tiny wiggles (a range under
    `min_range`) and the first `warmup_s` seconds never fire.
    """

    SLOT_S = 0.25  # the CC chatters ~70x a second; keep the max of each quarter second

    def __init__(self, window_s: float = 60.0, warmup_s: float = 8.0, min_range: int = 12,
                 high: float = 0.8, low: float = 0.5):
        self.window_s, self.warmup_s, self.min_range = window_s, warmup_s, min_range
        self.high, self.low = high, low
        self._slots: deque[list] = deque()  # [slot index, max value in that slot]
        self._first_t: float | None = None
        self._armed = True
        self.lo: float | None = None
        self.hi: float | None = None

    def _range(self) -> tuple[float, float] | None:
        values = sorted(v for _, v in self._slots)
        if len(values) < 8:
            return None
        pick = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]
        return pick(0.05), pick(0.95)

    def update(self, t: float, value: int) -> bool:
        """Feed one reading (t in seconds); True if this reading is a peak."""
        if self._first_t is None:
            self._first_t = t
        slot = int(t / self.SLOT_S)
        if self._slots and self._slots[-1][0] == slot:
            self._slots[-1][1] = max(self._slots[-1][1], value)
        else:
            self._slots.append([slot, value])
        oldest = int((t - self.window_s) / self.SLOT_S)
        while self._slots and self._slots[0][0] < oldest:
            self._slots.popleft()

        r = self._range()
        self.lo, self.hi = (r if r else (None, None))
        if r is None or t - self._first_t < self.warmup_s or r[1] - r[0] < self.min_range:
            return False
        lo, hi = r
        if self._armed and value >= lo + self.high * (hi - lo):
            self._armed = False
            return True
        if not self._armed and value <= lo + self.low * (hi - lo):
            self._armed = True
        return False

    def describe(self) -> str:
        if self.lo is None:
            return "auto: still learning the range"
        if self.hi - self.lo < self.min_range:
            return f"auto: range {self.lo:.0f}-{self.hi:.0f} too flat to peak"
        mark = self.lo + self.high * (self.hi - self.lo)
        return f"auto: range {self.lo:.0f}-{self.hi:.0f}, peak at {mark:.0f}"


class FeedbackListener:
    """Collects one CC (e.g. an envelope follower via the Hapax) on a MIDI input, in the background.

    `peak` is a fixed CC value ("a peak is >= 91"), "auto" (see AutoPeakDetector),
    or None. At most one peak is announced per phrase window.

    Polls RtMidi's own queue from a Python thread rather than registering a
    callback. A callback runs Python on CoreMIDI's thread, and closing the
    port while a message is arriving can deadlock (the closing thread holds
    the GIL; CoreMIDI waits for the callback, which waits for the GIL).
    """

    POLL_SECONDS = 0.01

    def __init__(self, port_name: str, channel: int, cc: int, peak: int | str | None = None,
                 debug: bool = False, on_peak=None, min_gap: float = 0.0):
        self.status = 0xB0 | (channel - 1)  # control change on this channel
        self.cc = cc
        self.peak = peak
        self.detector = AutoPeakDetector() if peak == "auto" else None
        self.debug = debug
        self.on_peak = on_peak  # called (from the polling thread) the first time a phrase peaks
        self._lock = threading.Lock()
        self._values: list[int] = []
        self._peaks = 0
        self.min_gap = min_gap          # at least this many seconds between announced peaks
        self._last_peak_t = -1e9
        self.t0 = time.monotonic()

        self._midi_in = rtmidi.MidiIn()
        self._midi_in.open_port(self._midi_in.get_ports().index(port_name))  # sysex/clock/sensing ignored
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self):
        while not self._stop.is_set():
            message = self._midi_in.get_message()
            if message is None:
                time.sleep(self.POLL_SECONDS)
                continue
            data = message[0]
            if len(data) == 3 and data[0] == self.status and data[1] == self.cc:
                self._record(data[2])

    def _record(self, value: int):
        t = time.monotonic() - self.t0
        with self._lock:
            self._values.append(value)
            if self.detector is not None:
                is_peak = self.detector.update(t, value)
            else:
                is_peak = self.peak is not None and value >= self.peak
            announce = is_peak and self._peaks == 0 and t - self._last_peak_t >= self.min_gap
            if announce:
                self._peaks += 1
                self._last_peak_t = t
        if self.debug:
            print(f"  [feedback {t:7.2f}s] CC{self.cc} = {value}")
        if announce:
            level = self.detector.describe() if self.detector else f">= {self.peak}"
            print(f"  [feedback {t:7.2f}s] PEAK: CC{self.cc} = {value} ({level})")
            if self.on_peak:
                self.on_peak(value)

    def take_window(self) -> Window:
        """Summarize everything received since the last call, and start a new window."""
        with self._lock:
            values, self._values = self._values, []
            peaks, self._peaks = self._peaks, 0
            marks = self.detector.describe() if self.detector else ""
        if not values:
            return Window(0, None, None, None, peaks, marks)
        return Window(len(values), min(values), max(values), sum(values) / len(values), peaks, marks)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)
        self._midi_in.close_port()
