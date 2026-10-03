"""Phrase composers: Claude (Anthropic API) and an offline mock."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

import anthropic

from music import NOTE_NAMES, Note, note_name


DEFAULT_MODEL = "claude-sonnet-5-5"


@dataclass
class Settings:
    root: int            # pitch class 0-11
    mode: str
    low: int             # MIDI note
    high: int            # MIDI note
    bpm: float
    beats: float         # phrase length
    style: str           # free-text mood hints, may be empty
    scale: list[int]     # allowed MIDI notes (scale within range)


@dataclass
class Phrase:
    notes: list[Note]
    intent: str          # one-line description of the idea, for display


PHRASE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": "One short sentence describing the musical idea of this phrase.",
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pitch": {"type": "integer", "description": "MIDI note number"},
                    "start": {"type": "number", "description": "Start time in beats from the phrase start"},
                    "duration": {"type": "number", "description": "Length in beats"},
                    "velocity": {"type": "integer", "description": "MIDI velocity 1-127"},
                },
                "required": ["pitch", "start", "duration", "velocity"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["intent", "notes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You compose slow ambient phrases for a single monophonic analog synth voice \
in a Eurorack system. Your notes are sent live over MIDI to a module that \
turns them into one pitch CV and one gate, so only one note can sound at a \
time: notes must never overlap.

Write music that breathes: long tones, generous silence, unhurried motion, \
and a clear contour. Favor stepwise movement and occasional leaps of a \
fourth or fifth. Let some notes ring for several beats and leave rests \
between gestures. Use velocity for gentle dynamic shape (it may drive a VCA \
or filter), mostly in the 40-100 range.

When you are given the previous phrase, develop it rather than starting \
over: keep a recognizable motif, rhythm, or contour and vary it (transpose \
within the scale, invert, stretch, fragment, or answer it), so consecutive \
phrases sound like one evolving piece."""


def _settings_text(s: Settings) -> str:
    scale_names = ", ".join(f"{note_name(p)}={p}" for p in s.scale)
    lines = [
        f"Key: {NOTE_NAMES[s.root]} {s.mode}",
        f"Range: {note_name(s.low)} ({s.low}) to {note_name(s.high)} ({s.high})",
        f"Allowed notes (name=MIDI number): {scale_names}",
        f"Tempo: {s.bpm:g} BPM",
        f"Phrase length: {s.beats:g} beats. Every note must start at or after beat 0 "
        f"and end by beat {s.beats:g}.",
    ]
    if s.style:
        lines.append(f"Mood / style hints: {s.style}")
    return "\n".join(lines)


class ClaudeComposer:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.client = anthropic.Anthropic()
        self.model = model

    def compose(self, s: Settings, previous: Phrase | None) -> Phrase:
        prompt = _settings_text(s)
        if previous is not None:
            prev = json.dumps([n.to_dict() for n in previous.notes])
            prompt += (
                f"\n\nPrevious phrase (\"{previous.intent}\"):\n{prev}\n\n"
                "Compose the next phrase, developing from this one."
            )
        else:
            prompt += "\n\nCompose the opening phrase."

        # Server-side fallback: if the model declines (very unlikely for music),
        # the API retries on a fallback model within the same call.
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": PHRASE_SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined to compose this phrase: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Claude's response was cut off (max_tokens) - try a shorter phrase.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError(f"No text in Claude's response (stop_reason={response.stop_reason})")

        data = json.loads(text)
        notes = [Note(**n) for n in data["notes"]]
        return Phrase(notes=notes, intent=data.get("intent", ""))


class MockComposer:
    """Offline stand-in: a slow random walk on the scale, varying the previous phrase."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def compose(self, s: Settings, previous: Phrase | None) -> Phrase:
        if previous is not None and previous.notes:
            return self._vary(s, previous)
        notes = []
        t = 0.0
        idx = self.rng.randrange(len(s.scale) // 3, 2 * len(s.scale) // 3)
        while t < s.beats - 1:
            dur = self.rng.choice([2, 3, 4, 4, 6, 8])
            notes.append(Note(s.scale[idx], t, dur, self.rng.randint(50, 95)))
            t += dur + self.rng.choice([0, 0, 1, 2, 4])
            idx = max(0, min(len(s.scale) - 1, idx + self.rng.choice([-2, -1, -1, 1, 1, 2, 3, -3])))
        return Phrase(notes=notes, intent="mock: random walk on the scale")

    def _vary(self, s: Settings, previous: Phrase) -> Phrase:
        shift = self.rng.choice([-2, -1, 0, 1, 2])
        notes = []
        for n in previous.notes:
            idx = min(range(len(s.scale)), key=lambda i: abs(s.scale[i] - n.pitch))
            if self.rng.random() < 0.3:
                idx += self.rng.choice([-1, 1])
            idx = max(0, min(len(s.scale) - 1, idx + shift))
            notes.append(Note(s.scale[idx], n.start, n.duration, n.velocity))
        return Phrase(notes=notes, intent=f"mock: previous phrase shifted {shift:+d} scale steps")
