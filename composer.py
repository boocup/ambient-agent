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
class Track:
    name: str            # the voice's role, e.g. "melody" or "bass"; also its key in the JSON
    channel: int         # MIDI channel, 1-16
    low: int             # MIDI note
    high: int            # MIDI note
    scale: list[int]     # allowed MIDI notes (scale within range)


@dataclass
class Settings:
    root: int            # pitch class 0-11
    mode: str
    bpm: float
    beats: float         # phrase length
    style: str           # free-text mood hints, may be empty
    tracks: list[Track]  # one monophonic voice each
    note: str = ""       # one-off instruction for this phrase (e.g. a key change)


@dataclass
class Phrase:
    parts: dict[str, list[Note]]  # track name -> notes
    intent: str                   # one-line description of the idea, for display


NOTES_SCHEMA = {
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
}


def phrase_schema(tracks: list[Track]) -> dict:
    """{"intent": "...", "<track name>": [notes], ...} - one note list per voice."""
    properties = {
        "intent": {
            "type": "string",
            "description": "One short sentence describing the musical idea of this phrase.",
        },
    }
    for t in tracks:
        properties[t.name] = {**NOTES_SCHEMA, "description": f"Notes for the {t.name} voice"}
    return {
        "type": "object",
        "properties": properties,
        "required": ["intent", *(t.name for t in tracks)],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """\
You compose slow ambient phrases for monophonic analog synth voices. Each \
voice is its own synth (or a Eurorack MIDI-to-CV module turning notes into \
one pitch CV and one gate), so within a voice only one note can sound at a \
time: a voice's notes must never overlap.

Write music that breathes: long tones, generous silence, unhurried motion, \
and a clear contour. Favor stepwise movement and occasional leaps of a \
fourth or fifth. Let some notes ring for several beats and leave rests \
between gestures. Use velocity for gentle dynamic shape (it may drive a VCA \
or filter), mostly in the 40-100 range.

When there are several voices, make them complement each other rather than \
double: give each its own register and rhythm, let one move while another \
holds or rests, and favor consonant meetings (fifths, octaves, thirds, sixths) \
where their notes overlap in time.

When you are given the previous phrase, develop it rather than starting \
over: keep a recognizable motif, rhythm, or contour and vary it (transpose \
within the scale, invert, stretch, fragment, or answer it), so consecutive \
phrases sound like one evolving piece."""


def _settings_text(s: Settings) -> str:
    lines = [
        f"Key: {NOTE_NAMES[s.root]} {s.mode}",
        f"Tempo: {s.bpm:g} BPM",
        f"Phrase length: {s.beats:g} beats. Every note must start at or after beat 0 "
        f"and end by beat {s.beats:g}.",
        "",
        "Voices (write a separate note list for each):" if len(s.tracks) > 1 else "Voice:",
    ]
    for t in s.tracks:
        scale_names = ", ".join(f"{note_name(p)}={p}" for p in t.scale)
        lines.append(
            f"- {t.name}: range {note_name(t.low)} ({t.low}) to {note_name(t.high)} ({t.high}). "
            f"Allowed notes (name=MIDI number): {scale_names}"
        )
    if s.style:
        lines.append(f"\nMood / style hints: {s.style}")
    if s.note:
        lines.append(f"\nFor this phrase: {s.note}")
    return "\n".join(lines)


def _user_prompt(s: Settings, previous: Phrase | None) -> str:
    prompt = _settings_text(s)
    if previous is not None:
        prev = json.dumps({name: [n.to_dict() for n in notes] for name, notes in previous.parts.items()})
        prompt += (
            f"\n\nPrevious phrase (\"{previous.intent}\"):\n{prev}\n\n"
            "Compose the next phrase, developing from this one."
        )
    else:
        prompt += "\n\nCompose the opening phrase."
    return prompt


def _parse_phrase(text: str, tracks: list[Track]) -> Phrase:
    data = json.loads(text)
    parts = {t.name: [Note(**n) for n in data.get(t.name) or []] for t in tracks}
    return Phrase(parts=parts, intent=data.get("intent", ""))


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
                "format": {"type": "json_schema", "schema": phrase_schema(s.tracks)},
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
        return _parse_phrase(text, s.tracks)


# Small local models follow a concrete example far better than a description.
_EXAMPLE_UPPER = [
    {"pitch": 50, "start": 0, "duration": 5, "velocity": 55},
    {"pitch": 55, "start": 6, "duration": 3, "velocity": 68},
    {"pitch": 57, "start": 9.5, "duration": 1.5, "velocity": 74},
    {"pitch": 60, "start": 12, "duration": 4, "velocity": 82},
    {"pitch": 57, "start": 18, "duration": 2, "velocity": 66},
    {"pitch": 53, "start": 21, "duration": 3, "velocity": 58},
    {"pitch": 52, "start": 25, "duration": 1, "velocity": 50},
    {"pitch": 50, "start": 27, "duration": 4.5, "velocity": 45},
]
_EXAMPLE_LOWER = [
    {"pitch": 38, "start": 0, "duration": 10, "velocity": 60},
    {"pitch": 33, "start": 12, "duration": 6, "velocity": 55},
    {"pitch": 36, "start": 20, "duration": 4, "velocity": 50},
    {"pitch": 38, "start": 26, "duration": 6, "velocity": 58},
]


def _ollama_example(tracks: list[Track]) -> str:
    example = {"intent": "A low call that climbs by fourth, lingers, and falls back"}
    for i, t in enumerate(tracks):
        example[t.name] = _EXAMPLE_UPPER if i == 0 else _EXAMPLE_LOWER
    return (
        "\n\nExample of the shape wanted (D dorian, 32 beats) - vary note count, lengths "
        "(1-10 beats) and rests, use each voice's own range; don't copy it:\n"
        + json.dumps(example)
    )


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
            "format": phrase_schema(s.tracks),
            "options": {"temperature": 0.8},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + _ollama_example(s.tracks)},
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
            return _parse_phrase(reply["message"]["content"], s.tracks)
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"{self.model} returned an unusable phrase ({e}); try again or another model.")


class MockComposer:
    """Offline stand-in: a slow random walk on each voice's scale, varying the previous phrase."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def compose(self, s: Settings, previous: Phrase | None) -> Phrase:
        parts = {}
        for t in s.tracks:
            prev = previous.parts.get(t.name) if previous else None
            parts[t.name] = self._vary(t, prev) if prev else self._walk(t, s.beats)
        intent = "mock: previous phrase varied" if previous else "mock: random walk on the scale"
        return Phrase(parts=parts, intent=intent)

    def _walk(self, t: Track, beats: float) -> list[Note]:
        notes = []
        time = 0.0
        idx = self.rng.randrange(len(t.scale) // 3, 2 * len(t.scale) // 3)
        while time < beats - 1:
            dur = self.rng.choice([2, 3, 4, 4, 6, 8])
            notes.append(Note(t.scale[idx], time, dur, self.rng.randint(50, 95)))
            time += dur + self.rng.choice([0, 0, 1, 2, 4])
            idx = max(0, min(len(t.scale) - 1, idx + self.rng.choice([-2, -1, -1, 1, 1, 2, 3, -3])))
        return notes

    def _vary(self, t: Track, previous: list[Note]) -> list[Note]:
        shift = self.rng.choice([-2, -1, 0, 1, 2])
        notes = []
        for n in previous:
            idx = min(range(len(t.scale)), key=lambda i: abs(t.scale[i] - n.pitch))
            if self.rng.random() < 0.3:
                idx += self.rng.choice([-1, 1])
            idx = max(0, min(len(t.scale) - 1, idx + shift))
            notes.append(Note(t.scale[idx], n.start, n.duration, n.velocity))
        return notes
