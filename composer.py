"""Phrase composers: Claude (Anthropic API), a local model (Ollama), and an offline mock."""

from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from dataclasses import dataclass

import anthropic

from music import Note, note_name, pc_name


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
    note: str = ""       # one-off instruction for this phrase (e.g. a key change, or its role in the form)
    history: str = ""    # compact memory of recent phrases, and any motif to echo
    rhythm: dict[str, list[float]] | None = None  # track -> start times fixed by the script (applied afterwards)


@dataclass
class Phrase:
    parts: dict[str, list[Note]]  # track name -> notes
    intent: str                   # one-line description of the idea, for display
    compose_seconds: float = 0.0  # how long composing it took (set by the agent)


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

When you are given the previous phrase and your role is to develop it, develop it rather than starting \
over: keep a recognizable motif, rhythm, or contour and vary it (transpose \
within the scale, invert, stretch, fragment, or answer it), so consecutive \
phrases sound like one evolving piece.

A request may also name the phrase's role in a larger form (statement, \
contrast, space, return) and list the recent phrases. Obey the role. Unless \
the role says to develop or return to something, never copy the rhythm or \
contour of a recent phrase: change how many notes you write, their lengths, \
where the rests fall, and where the line peaks. Staying inside the register \
given by the allowed notes is part of the role."""


def _settings_text(s: Settings) -> str:
    lines = [
        f"Key: {pc_name(s.root)} {s.mode}",
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
    if s.history:
        lines.append("\n" + s.history)
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
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "medium"):
        self.effort = effort
        # A stalled connection must not hang a live session (the library's default timeout is 10 minutes).
        self.client = anthropic.Anthropic(timeout=45.0, max_retries=2)
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
                "effort": self.effort,
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


# Small local models follow a concrete example far better than a description -
# and tend to copy it outright. So the example is freshly randomized for every
# request: copying it still gives a different phrase each time.
_EXAMPLE_INTENTS = [
    "A slow rise to a held tone, then a long sigh back down",
    "Two short calls, a long silence, and a single low answer",
    "A drifting line that circles one note before settling lower",
    "Wide leaps softened by long rests, ending on the root",
    "A quiet stepwise climb that stops short and fades",
    "A held low tone, a brief flicker above, and stillness",
]
_example_rng = random.Random()


def _example_part(scale: list[int], beats: float, upper: bool) -> list[dict]:
    rng = _example_rng
    durations = [1, 1.5, 2, 3, 4, 5, 6] if upper else [4, 5, 6, 8, 10]
    rests = [0.5, 1, 1.5, 2, 3, 4] if upper else [1, 2, 3, 4]
    steps = [-3, -2, -1, -1, 1, 1, 2, 3] if upper else [-2, -1, 1, 2]
    idx = rng.randrange(len(scale) // 4, max(len(scale) // 4 + 1, 3 * len(scale) // 4))
    notes, t = [], float(rng.choice([0, 0, 1, 2]))
    while True:
        d = rng.choice(durations)
        if t + d > beats:
            break
        notes.append({"pitch": scale[idx], "start": t, "duration": d, "velocity": rng.randint(45, 85)})
        t += d + rng.choice(rests)
        idx = max(0, min(len(scale) - 1, idx + rng.choice(steps)))
    return notes


def _ollama_example(s: Settings) -> str:
    """Two different random examples: small models learn the pattern (uneven lengths,
    rests, gentle contour) from a pair instead of copying a single one."""
    examples = []
    for intent in _example_rng.sample(_EXAMPLE_INTENTS, 2):
        example = {"intent": intent}
        for i, t in enumerate(s.tracks):
            example[t.name] = _example_part(t.scale, s.beats, upper=(i == 0))
        examples.append(json.dumps(example))
    return (
        "\n\nTwo examples of the kind of phrase wanted - uneven note lengths, real rests, "
        "a gentle contour. Write your own in the same spirit:\n" + "\n".join(examples)
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
                {"role": "system", "content": SYSTEM_PROMPT + _ollama_example(s)},
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
