"""Feedback from the rack: listens for one MIDI CC and summarizes it per phrase."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import mido


@dataclass
class Window:
    """What the CC did during one phrase."""
    count: int
    low: int | None
    high: int | None
    average: float | None

    def describe(self) -> str:
        if not self.count:
            return "no CC received"
        return f"{self.count} msgs, min {self.low}, avg {self.average:.0f}, max {self.high}"


class FeedbackListener:
    """Collects one CC (e.g. an envelope follower via the Hapax) on a MIDI input, in the background."""

    def __init__(self, port_name: str, channel: int, cc: int, peak: int | None = None, debug: bool = False):
        self.channel = channel - 1  # 0-15
        self.cc = cc
        self.peak = peak
        self.debug = debug
        self._lock = threading.Lock()
        self._values: list[int] = []
        self._peak_announced = False
        self.t0 = time.monotonic()
        self.port = mido.open_input(port_name, callback=self._on_message)

    def _on_message(self, msg: mido.Message):
        if msg.type != "control_change" or msg.channel != self.channel or msg.control != self.cc:
            return
        with self._lock:
            self._values.append(msg.value)
            first_peak = self.peak is not None and msg.value >= self.peak and not self._peak_announced
            if first_peak:
                self._peak_announced = True
        t = time.monotonic() - self.t0
        if self.debug:
            print(f"  [feedback {t:7.2f}s] CC{self.cc} = {msg.value}")
        if first_peak:
            print(f"  [feedback {t:7.2f}s] PEAK: CC{self.cc} = {msg.value} (>= {self.peak})")

    def take_window(self) -> Window:
        """Summarize everything received since the last call, and start a new window."""
        with self._lock:
            values, self._values = self._values, []
            self._peak_announced = False
        if not values:
            return Window(0, None, None, None)
        return Window(len(values), min(values), max(values), sum(values) / len(values))

    def close(self):
        self.port.close()
