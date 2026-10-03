"""Phrase composers: Claude (Anthropic API), a local model (Ollama), and an offline mock."""

from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from dataclasses import dataclass

import anthropic

from music import NOTE_NAMES, Note, note_name


DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_URL = "http://localhost:11434"


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


def _user_prompt(s: Settings, previous: Phrase | None) -> str:
    prompt = _settings_text(s)
    if previous is not None:
        prev = json.dumps([n.to_dict() for n in previous.notes])
        prompt += (
            f"\n\nPrevious phrase (\"{previous.intent}\"):\n{prev}\n\n"
            "Compose the next phrase, developing from this one."
        )
    else:
        prompt += "\n\nCompose the opening phrase."
    return prompt


def _parse_phrase(text: str) -> Phrase:
    data = json.loads(text)
    notes = [Note(**n) for n in data["notes"]]
    return Phrase(notes=notes, intent=data.get("intent", ""))


class ClaudeComposer:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.client = anthropic.Anthropic()
        self.model = model

    def compose(self, s: Settings, previous: Phrase | None) -> Phrase:
        prompt = _user_prompt(s, previous)

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
        return _parse_phrase(text)


# Small local models follow a concrete example far better than a description.
OLLAMA_EXAMPLE = """\

Example of the kind of phrase wanted (D dorian, 32 beats) - vary note count \
(6-12), lengths (1-6 beats) and rests; don't copy it:
{"intent": "A low call that climbs by fourth, lingers, and falls back", "notes": [
 {"pitch": 50, "start": 0, "duration": 5, "velocity": 55},
 {"pitch": 55, "start": 6, "duration": 3, "velocity": 68},
 {"pitch": 57, "start": 9.5, "duration": 1.5, "velocity": 74},
 {"pitch": 60, "start": 12, "duration": 4, "velocity": 82},
 {"pitch": 57, "start": 18, "duration": 2, "velocity": 66},
 {"pitch": 53, "start": 21, "duration": 3, "velocity": 58},
 {"pitch": 52, "start": 25, "duration": 1, "velocity": 50},
 {"pitch": 50, "start": 27, "duration": 4.5, "velocity": 45}]}"""


class OllamaComposer:
    """Runs a local model through Ollama (https://ollama.com). Free and offline."""

    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL, url: str = OLLAMA_URL):
        self.model = model
        self.url = url

    def compose(self, s: Settings, previous: Phrase | None) -> Phrase:
        body = {
            "model": self.model,
            "stream": False,
            # Ollama constrains the output to this JSON schema.
            "format": PHRASE_SCHEMA,
            "options": {"temperature": 0.8},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + "\n" + OLLAMA_EXAMPLE},
                {"role": "user", "content": _user_prompt(s, previous)},
            ],
        }
        req = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                reply = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 404:
                raise RuntimeError(f"Ollama doesn't have {self.model!r}. Run: ollama pull {self.model}")
            raise RuntimeError(f"Ollama error {e.code}: {detail}")
        except urllib.error.URLError:
            raise RuntimeError(f"Couldn't reach Ollama at {self.url} - is the Ollama app running?")

        try:
            return _parse_phrase(reply["message"]["content"])
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"{self.model} returned an unusable phrase ({e}); try again or another model.")


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
