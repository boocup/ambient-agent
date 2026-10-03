"""Feedback from the rack: listens for one MIDI CC and summarizes it per phrase."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import rtmidi


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
    """Collects one CC (e.g. an envelope follower via the Hapax) on a MIDI input, in the background.

    Polls RtMidi's own queue from a Python thread rather than registering a
    callback. A callback runs Python on CoreMIDI's thread, and closing the
    port while a message is arriving can deadlock (the closing thread holds
    the GIL; CoreMIDI waits for the callback, which waits for the GIL).
    """

    POLL_SECONDS = 0.01

    def __init__(self, port_name: str, channel: int, cc: int, peak: int | None = None, debug: bool = False):
        self.status = 0xB0 | (channel - 1)  # control change on this channel
        self.cc = cc
        self.peak = peak
        self.debug = debug
        self._lock = threading.Lock()
        self._values: list[int] = []
        self._peak_announced = False
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
        with self._lock:
            self._values.append(value)
            first_peak = self.peak is not None and value >= self.peak and not self._peak_announced
            if first_peak:
                self._peak_announced = True
        t = time.monotonic() - self.t0
        if self.debug:
            print(f"  [feedback {t:7.2f}s] CC{self.cc} = {value}")
        if first_peak:
            print(f"  [feedback {t:7.2f}s] PEAK: CC{self.cc} = {value} (>= {self.peak})")

    def take_window(self) -> Window:
        """Summarize everything received since the last call, and start a new window."""
        with self._lock:
            values, self._values = self._values, []
            self._peak_announced = False
        if not values:
            return Window(0, None, None, None)
        return Window(len(values), min(values), max(values), sum(values) / len(values))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1)
        self._midi_in.close_port()
