"""MIDI output: port selection, real-time phrase playback, panic, .mid export."""

from __future__ import annotations

import threading
import time

import mido

from music import Note, note_name


def output_names() -> list[str]:
    return mido.get_output_names()


def find_port(name: str, names: list[str] | None = None, kind: str = "output") -> str:
    """Pick a port by exact name, or by a unique case-insensitive substring."""
    names = output_names() if names is None else names
    if name in names:
        return name
    matches = [n for n in names if name.lower() in n.lower()]
    if len(matches) == 1:
        return matches[0]
    available = "\n  ".join(names) or "(none)"
    if not matches:
        raise SystemExit(f"No MIDI {kind} matches {name!r}. Available:\n  {available}")
    raise SystemExit(f"{name!r} matches several outputs - be more specific:\n  " + "\n  ".join(matches))


class DryRunPort:
    """Stands in for a MIDI port and prints what would be sent."""

    def __init__(self):
        self.t0 = time.monotonic()

    def send(self, msg: mido.Message):
        t = time.monotonic() - self.t0
        ch = f"ch{msg.channel + 1:<2}"
        if msg.type == "note_on":
            print(f"  [{t:7.2f}s] {ch} gate ON   {note_name(msg.note):<4} vel {msg.velocity}")
        elif msg.type == "note_off":
            print(f"  [{t:7.2f}s] {ch} gate off  {note_name(msg.note)}")
        elif msg.type == "control_change" and msg.control == 123:
            print(f"  [{t:7.2f}s] {ch} All Notes Off")
        elif msg.type == "control_change":
            print(f"  [{t:7.2f}s] {ch} CC{msg.control} = {msg.value}")

    def close(self):
        pass


class Player:
    """Plays phrases on one or more channels, each strictly monophonic."""

    def __init__(self, port, channels: list[int], bpm: float):
        self.port = port
        self.bpm = bpm
        self.sounding: dict[int, int | None] = {ch: None for ch in channels}  # channel (0-15) -> gated note
        self._lock = threading.Lock()

    def _send(self, msg_type: str, channel: int, note: int, velocity: int = 0):
        with self._lock:
            self.port.send(mido.Message(msg_type, channel=channel, note=note, velocity=velocity))
            self.sounding[channel] = note if msg_type == "note_on" else None

    def send_cc(self, channel: int, control: int, value: int):
        """Send a CC on any channel (0-15), safely alongside playback."""
        with self._lock:
            self.port.send(mido.Message("control_change", channel=channel, control=control, value=value))

    def play(self, parts: list[tuple[int, list[Note]]], length_beats: float):
        """Play one phrase - (channel, notes) per voice, all together - in real time.

        Returns when the phrase's full length has elapsed.
        """
        spb = 60.0 / self.bpm
        events = []
        for channel, notes in parts:
            for n in notes:
                events.append((n.start * spb, 1, channel, "note_on", n.pitch, n.velocity))
                events.append((n.end * spb, 0, channel, "note_off", n.pitch, 0))
        events.sort(key=lambda e: (e[0], e[1]))  # note_offs before note_ons at the same instant

        t0 = time.monotonic()
        for at, _, channel, msg_type, pitch, vel in events:
            self._sleep_until(t0 + at)
            self._send(msg_type, channel, pitch, vel)
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
            for channel, note in self.sounding.items():
                if note is not None:
                    self.port.send(mido.Message("note_off", channel=channel, note=note))
                    self.sounding[channel] = None
                self.port.send(mido.Message("control_change", channel=channel, control=123, value=0))


def save_midi(path: str, phrases: list[list[tuple[int, list[Note]]]], length_beats: float, bpm: float):
    """Write played phrases back-to-back as a .mid file, one track per channel."""
    tpb = 480
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)

    by_channel: dict[int, list] = {}
    for i, parts in enumerate(phrases):
        offset = i * length_beats
        for channel, notes in parts:
            events = by_channel.setdefault(channel, [])
            for n in notes:
                events.append((round((offset + n.start) * tpb), 1, "note_on", n.pitch, n.velocity))
                events.append((round((offset + n.end) * tpb), 0, "note_off", n.pitch, 0))

    end_tick = round(len(phrases) * length_beats * tpb)
    for i, (channel, events) in enumerate(sorted(by_channel.items())):
        track = mido.MidiTrack()
        mid.tracks.append(track)
        if i == 0:
            track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
        events.sort(key=lambda e: (e[0], e[1]))
        now = 0
        for tick, _, msg_type, pitch, vel in events:
            track.append(mido.Message(msg_type, channel=channel, note=pitch, velocity=vel, time=tick - now))
            now = tick
        track.append(mido.MetaMessage("end_of_track", time=max(0, end_tick - now)))
    mid.save(path)
