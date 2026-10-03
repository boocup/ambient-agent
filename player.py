"""MIDI output: port selection, real-time phrase playback, panic, .mid export."""

from __future__ import annotations

import threading
import time

import mido

from music import Note, note_name


def output_names() -> list[str]:
    return mido.get_output_names()


def find_port(name: str) -> str:
    """Pick an output by exact name, or by a unique case-insensitive substring."""
    names = output_names()
    if name in names:
        return name
    matches = [n for n in names if name.lower() in n.lower()]
    if len(matches) == 1:
        return matches[0]
    available = "\n  ".join(names) or "(none)"
    if not matches:
        raise SystemExit(f"No MIDI output matches {name!r}. Available:\n  {available}")
    raise SystemExit(f"{name!r} matches several outputs - be more specific:\n  " + "\n  ".join(matches))


class DryRunPort:
    """Stands in for a MIDI port and prints what would be sent."""

    def __init__(self):
        self.t0 = time.monotonic()

    def send(self, msg: mido.Message):
        t = time.monotonic() - self.t0
        if msg.type == "note_on":
            print(f"  [{t:7.2f}s] gate ON   {note_name(msg.note):<4} vel {msg.velocity}")
        elif msg.type == "note_off":
            print(f"  [{t:7.2f}s] gate off  {note_name(msg.note)}")
        elif msg.type == "control_change" and msg.control == 123:
            print(f"  [{t:7.2f}s] All Notes Off")

    def close(self):
        pass


class Player:
    def __init__(self, port, channel: int, bpm: float):
        self.port = port
        self.channel = channel  # 0-15
        self.bpm = bpm
        self.sounding: int | None = None  # the one note currently gated on
        self._lock = threading.Lock()

    def _send(self, msg_type: str, note: int, velocity: int = 0):
        with self._lock:
            self.port.send(mido.Message(msg_type, channel=self.channel, note=note, velocity=velocity))
            self.sounding = note if msg_type == "note_on" else None

    def play(self, notes: list[Note], length_beats: float):
        """Play one phrase in real time; returns when the phrase's full length has elapsed."""
        spb = 60.0 / self.bpm
        events = []
        for n in notes:
            events.append((n.start * spb, 1, "note_on", n.pitch, n.velocity))
            events.append((n.end * spb, 0, "note_off", n.pitch, 0))
        events.sort(key=lambda e: (e[0], e[1]))  # note_off before note_on at the same instant

        t0 = time.monotonic()
        for at, _, msg_type, pitch, vel in events:
            self._sleep_until(t0 + at)
            self._send(msg_type, pitch, vel)
        self._sleep_until(t0 + length_beats * spb)

    @staticmethod
    def _sleep_until(deadline: float):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def panic(self):
        """Release everything so no gate stays high."""
        with self._lock:
            if self.sounding is not None:
                self.port.send(mido.Message("note_off", channel=self.channel, note=self.sounding))
                self.sounding = None
            self.port.send(mido.Message("control_change", channel=self.channel, control=123, value=0))


def save_midi(path: str, phrases: list[list[Note]], length_beats: float, bpm: float, channel: int):
    """Write played phrases back-to-back as a single-track .mid file."""
    tpb = 480
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))

    events = []
    for i, notes in enumerate(phrases):
        offset = i * length_beats
        for n in notes:
            events.append((round((offset + n.start) * tpb), 1, "note_on", n.pitch, n.velocity))
            events.append((round((offset + n.end) * tpb), 0, "note_off", n.pitch, 0))
    events.sort(key=lambda e: (e[0], e[1]))

    now = 0
    for tick, _, msg_type, pitch, vel in events:
        track.append(mido.Message(msg_type, channel=channel, note=pitch, velocity=vel, time=tick - now))
        now = tick
    end_tick = round(len(phrases) * length_beats * tpb)
    track.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - now)))
    mid.save(path)
