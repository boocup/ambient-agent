"""Notes, scales, and phrase cleanup (range, scale, strict monophony)."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_FLATS = {flat: sharp for flat, sharp in zip(FLAT_NAMES, NOTE_NAMES) if flat != sharp}

# Spell black keys with flats (Bb, Eb...) instead of sharps; set by use_flats().
_spell_flats = False


def use_flats(flats: bool):
    """Choose how notes are spelled everywhere (display and prompts)."""
    global _spell_flats
    _spell_flats = flats


def pc_name(pc: int) -> str:
    """Pitch class 0-11 -> 'Bb' or 'A#', per use_flats()."""
    return (FLAT_NAMES if _spell_flats else NOTE_NAMES)[pc % 12]

# Semitone intervals from the root.
MODES = {
    "ionian": [0, 2, 4, 5, 7, 9, 11],
    "major": [0, 2, 4, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "major-pentatonic": [0, 2, 4, 7, 9],
    "minor-pentatonic": [0, 3, 5, 7, 10],
}

# Gap left between a trimmed note and the next one, so the gate actually
# drops and the next note retriggers the envelope.
GATE_GAP_BEATS = 0.05
MIN_DURATION_BEATS = 0.1


@dataclass
class Note:
    pitch: int        # MIDI note number
    start: float      # beats from the start of the phrase
    duration: float   # beats
    velocity: int     # 1-127

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict:
        return asdict(self)


def parse_note(name: str) -> int:
    """'C2' -> 36, 'F#3' -> 54, 'Bb4' -> 70. Middle C (C4) = 60."""
    m = re.fullmatch(r"\s*([A-Ga-g])([#b]?)(-?\d+)\s*", name)
    if not m:
        raise ValueError(f"Can't parse note name {name!r} (try e.g. C2, F#3, Bb4)")
    letter, accidental, octave = m.group(1).upper(), m.group(2), int(m.group(3))
    pc_name = _FLATS.get(letter + accidental, letter + accidental)
    return NOTE_NAMES.index(pc_name) + 12 * (octave + 1)


def note_name(pitch: int) -> str:
    return f"{pc_name(pitch)}{pitch // 12 - 1}"


def parse_key(key: str) -> tuple[int, str]:
    """'D dorian' -> (2, 'dorian'). Root is a pitch class 0-11."""
    parts = key.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Key should look like 'D dorian', got {key!r}")
    root_name, mode = parts[0], parts[1].lower()
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}. Known: {', '.join(MODES)}")
    root = parse_note(root_name + "4") % 12
    return root, mode


def scale_pitches(root: int, mode: str, low: int, high: int) -> list[int]:
    allowed = {(root + i) % 12 for i in MODES[mode]}
    return [p for p in range(low, high + 1) if p % 12 in allowed]


def snap_to_scale(pitch: int, scale: list[int]) -> int:
    return min(scale, key=lambda p: (abs(p - pitch), p))


def clean_phrase(notes: list[Note], length_beats: float, scale: list[int]) -> list[Note]:
    """Make a phrase safe to play on a single CV/gate voice.

    - drops notes starting outside the phrase, clips notes running past its end
    - snaps pitches into the scale and range
    - clamps velocity to 1-127
    - strictly monophonic: when notes overlap, the earlier one is trimmed to
      end GATE_GAP_BEATS before the next starts; same-start duplicates keep
      only the first
    """
    kept = []
    for n in notes:
        if n.start < 0 or n.start >= length_beats or n.duration <= 0:
            continue
        kept.append(Note(
            pitch=snap_to_scale(int(round(n.pitch)), scale),
            start=float(n.start),
            duration=min(float(n.duration), length_beats - n.start),
            velocity=max(1, min(127, int(round(n.velocity)))),
        ))
    kept.sort(key=lambda n: n.start)

    mono: list[Note] = []
    for n in kept:
        if mono and n.start <= mono[-1].start:
            continue  # same start as the previous note: keep the first
        if mono and mono[-1].end > n.start - GATE_GAP_BEATS:
            mono[-1].duration = n.start - GATE_GAP_BEATS - mono[-1].start
        mono.append(n)
    return [n for n in mono if n.duration >= MIN_DURATION_BEATS]


def format_phrase(notes: list[Note]) -> str:
    lines = [f"  {'beat':>6}  {'len':>5}  {'note':<4} {'vel':>3}"]
    for n in notes:
        lines.append(f"  {n.start:6.2f}  {n.duration:5.2f}  {note_name(n.pitch):<4} {n.velocity:3d}")
    return "\n".join(lines)
