"""Musical form for long listening sessions: sections, register windows, phrase memory, novelty.

Asking a model to "develop the previous phrase" every time pulls each phrase
toward the last one, and over a long session the music settles into a rut.
So the script, not the model, plans where we are in a larger shape and enforces
what it can:

- sections: statement (introduce, then develop a motif), contrast (leave it
  behind), space (mostly silence), return (bring the motif home, transformed)
- register windows: each section confines each voice to part of its range, and
  the confinement is applied to the allowed notes, so it can't be ignored
- memory: the model gets a compact summary of recent phrases (rhythm, contour,
  range), not just the previous one, and is told not to repeat them
- no anchoring: contrast and space phrases don't see the previous phrase at all
- novelty: a 0-1 score of how different a phrase is from the recent ones
"""

from __future__ import annotations

import dataclasses
import json
import random
from dataclasses import dataclass
from difflib import SequenceMatcher

from music import Note, note_name

# Fraction of a track's scale (low to high) each register window covers.
REGISTERS = {"low": (0.0, 0.55), "middle": (0.25, 0.75), "high": (0.45, 1.0), "wide": (0.0, 1.0)}
SECTION_LENGTH = {"statement": (2, 3), "contrast": (1, 2), "space": (1, 1), "return": (1, 2)}
NEXT_SECTION = {
    "statement": ["contrast", "contrast", "space"],
    "contrast": ["statement", "statement", "return"],
    "space": ["statement"],
    "return": ["contrast", "space", "statement"],
}
MIN_WINDOW_NOTES = 3


@dataclass
class Plan:
    section: str
    step: int                    # 1-based position within the section
    of: int                      # phrases in the section
    develop: bool                # show the model the previous phrase?
    echo_anchor: bool            # show the motif from the last statement? (return)
    registers: dict[str, str]    # track name -> register window
    density: str                 # sparse / medium / busy
    instruction: str
    rhythm: dict[str, list[float]] | None = None  # track name -> start times (beats) fixed by the script

    def label(self) -> str:
        regs = " ".join(f"{name}:{reg}" for name, reg in self.registers.items())
        return f"{self.section} {self.step}/{self.of} · {self.density} · {regs}"


class FormPlanner:
    """Walks through sections, choosing register windows and density as it goes."""

    def __init__(self, track_names: list[str], beats: float, rng: random.Random | None = None):
        self.names = track_names
        self.beats = beats
        self.rng = rng or random.Random()
        self._section: str | None = None
        self._left = 0
        self._of = 0
        self._step = 0
        self._registers = {n: "wide" for n in track_names}
        self._density = "medium"
        self._last_contrast_density = "sparse"

    def _density_range(self, label: str) -> tuple[int, int]:
        b = self.beats
        lo, hi = {"sparse": (b / 12, b / 8), "medium": (b / 6, b / 3.5), "busy": (b / 3.2, b / 2.2)}[label]
        return max(2, round(lo)), max(3, round(hi))

    def _start_section(self):
        previous = self._section
        self._section = "statement" if previous is None else self.rng.choice(NEXT_SECTION[previous])
        self._of = self.rng.randint(*SECTION_LENGTH[self._section])
        self._left, self._step = self._of, 0
        for i, name in enumerate(self.names):
            options = ["low", "middle", "high"] if i == 0 else ["low", "middle", "wide"]
            options = [o for o in options if o != self._registers[name]] or options
            self._registers[name] = self.rng.choice(options)
        if self._section == "contrast":
            self._density = "busy" if self._last_contrast_density == "sparse" else "sparse"
            self._last_contrast_density = self._density
        elif self._section == "space":
            self._density = "sparse"
        else:
            self._density = "medium"

    def _rhythm(self, history: list) -> dict[str, list[float]]:
        """Irregular start times for each voice, kept clear of the recent phrases' rhythms."""
        lo, hi = self._density_range(self._density)
        lead = self.rng.randint(lo, max(lo, hi))
        out = {}
        for i, name in enumerate(self.names):
            if i == 0:
                count = lead
            elif self._section == "space":
                count = self.rng.choice([0, 1])
            else:
                count = max(1, round(lead * self.rng.uniform(0.35, 0.6)))
            recent = [set(h.parts[name]["onsets"]) for h in history[-4:] if name in h.parts and h.parts[name]["n"]]
            out[name] = make_rhythm(self.rng, self.beats, count, recent)
        return out

    def next(self, history: list | None = None) -> Plan:
        if self._left == 0:
            self._start_section()
        self._step += 1
        self._left -= 1
        sec, step, of = self._section, self._step, self._of
        lo, hi = self._density_range(self._density)
        density = f"Density: {self._density} - roughly {lo}-{hi} notes in the lead voice, fewer in the others."
        if sec == "statement" and step == 1:
            text = (f"Role: STATEMENT {step} of {of}. Introduce a NEW motif of 3-5 notes that is clearly different "
                    f"from the recent phrases listed above. Do not continue the previous phrase. {density}")
            develop, echo = False, False
        elif sec == "statement":
            text = (f"Role: STATEMENT {step} of {of}. Develop the motif from the previous phrase - keep its identity "
                    f"but change the rhythm or the contour (stretch, fragment, invert, answer it); do not repeat it "
                    f"unchanged. {density}")
            develop, echo = True, False
        elif sec == "contrast":
            text = (f"Role: CONTRAST. Leave the motif behind. Use a new rhythm and a new contour, different from "
                    f"every recent phrase listed above, so the music surprises the listener gently. {density}")
            develop, echo = False, False
        elif sec == "space":
            text = ("Role: SPACE. Mostly silence: 2-4 long notes in the lead voice, and the other voices hold a "
                    "single tone or rest entirely. Let the listener breathe. " + density)
            develop, echo = False, False
        else:  # return
            text = (f"Role: RETURN {step} of {of}. Bring back the earlier motif shown below, transformed (different "
                    f"register or rhythm), so the piece feels like it comes home. {density}")
            develop, echo = False, True
        rhythm = self._rhythm(history or [])
        starts = "; ".join(f"{name}: " + (", ".join(f"{b:g}" for b in beats) if beats else "rest the whole phrase")
                           for name, beats in rhythm.items())
        text += (f" Rhythm is fixed by the score - write exactly one note at each start time (in beats): {starts}. "
                 f"Choose each note's pitch, length (it may stop early to leave a rest) and velocity.")
        return Plan(sec, step, of, develop, echo, dict(self._registers), self._density, text, rhythm)


def make_rhythm(rng: random.Random, beats: float, count: int, recent: list[set]) -> list[float]:
    """`count` irregular start times (half-beat grid) across the phrase, as unlike the recent rhythms as we can get."""
    if count <= 0:
        return []
    avg = beats / (count + 0.3)
    best, best_sim = [], 2.0
    for _ in range(14):
        onsets, t = [], float(rng.choice([0, 0, 0, 0.5, 1, 1.5, 2]))
        while len(onsets) < count and t <= beats - 1.5:
            onsets.append(round(t * 2) / 2)
            t += max(1.0, round(rng.uniform(0.55, 1.6) * avg * 2) / 2)
        sim = max((len(set(onsets) & r) / len(set(onsets) | r) for r in recent), default=0.0)
        if sim < best_sim:
            best, best_sim = onsets, sim
        if sim <= 0.25:
            break
    return best


def snap_to_rhythm(notes: list[Note], starts: list[float], beats: float) -> list[Note]:
    """Put the model's notes, in order, on the script's start times. Extra notes are dropped; if the model wrote
    fewer notes than start times, the first start times are used."""
    notes = sorted(notes, key=lambda n: n.start)
    out = []
    for note, start in zip(notes, sorted(starts)):
        out.append(Note(note.pitch, float(start), max(0.1, min(float(note.duration), beats - start)), note.velocity))
    return out


def window_tracks(tracks, registers: dict[str, str]):
    """The same tracks with allowed notes confined to a register window of each track's scale."""
    out = []
    for t in tracks:
        lo_f, hi_f = REGISTERS[registers.get(t.name, "wide")]
        n = len(t.scale)
        sub = t.scale[int(lo_f * (n - 1)): int(round(hi_f * (n - 1))) + 1]
        if len(sub) < MIN_WINDOW_NOTES:
            sub = t.scale
        out.append(dataclasses.replace(t, scale=sub, low=min(sub), high=max(sub)))
    return out


# --- memory and novelty -------------------------------------------------------------------------

@dataclass
class PhraseSummary:
    number: int
    role: str
    parts: dict[str, dict]  # track -> {"n", "low", "high", "contour", "onsets"}

    def line(self) -> str:
        bits = []
        for name, p in self.parts.items():
            if not p["n"]:
                bits.append(f"{name}: silent")
            else:
                bits.append(f"{name}: {p['n']} notes {p['low']}-{p['high']}, contour {p['contour'] or '-'}, "
                            f"onsets {' '.join(map(str, p['onsets']))}")
        return f"#{self.number} {self.role}: " + " | ".join(bits)


def summarize(number: int, role: str, parts: dict[str, list[Note]]) -> PhraseSummary:
    out = {}
    for name, notes in parts.items():
        notes = sorted(notes, key=lambda n: n.start)
        pitches = [n.pitch for n in notes]
        steps = "".join("u" if b > a else "d" if b < a else "s" for a, b in zip(pitches, pitches[1:]))
        out[name] = {
            "n": len(notes),
            "low": note_name(min(pitches)) if pitches else "",
            "high": note_name(max(pitches)) if pitches else "",
            "contour": steps[:8],
            "onsets": [round(n.start) for n in notes],
        }
    return PhraseSummary(number, role, out)


def similarity(a: PhraseSummary, b: PhraseSummary) -> float:
    """0 = nothing alike, 1 = same rhythm and contour in every voice."""
    scores = []
    for name, pa in a.parts.items():
        pb = b.parts.get(name)
        if pb is None:
            continue
        if not pa["n"] and not pb["n"]:
            scores.append(1.0)
            continue
        if not pa["n"] or not pb["n"]:
            scores.append(0.0)
            continue
        sa, sb = set(pa["onsets"]), set(pb["onsets"])
        jaccard = len(sa & sb) / len(sa | sb)
        contour = SequenceMatcher(None, pa["contour"], pb["contour"]).ratio() if pa["contour"] or pb["contour"] else 1.0
        scores.append(0.6 * jaccard + 0.4 * contour)
    return sum(scores) / len(scores) if scores else 0.0


def novelty(new: PhraseSummary, recent: list[PhraseSummary], window: int = 4) -> float:
    """1 - how alike the new phrase is to its closest match among the last few. 1.0 = brand new."""
    return 1.0 - max((similarity(new, r) for r in recent[-window:]), default=0.0)


def history_text(history: list[PhraseSummary], n: int) -> str:
    if not history or n <= 0:
        return ""
    lines = ["Recent phrases, oldest first (do NOT repeat their rhythms or contours unless your role says so):"]
    lines += ["  " + h.line() for h in history[-n:]]
    return "\n".join(lines)


def anchor_text(number: int, parts: dict[str, list[Note]]) -> str:
    """The notes of an earlier phrase, compact, for a RETURN section to echo."""
    bits = []
    for name, notes in parts.items():
        notes = sorted(notes, key=lambda n: n.start)
        bits.append(f"{name}: " + " ".join(f"{note_name(n.pitch)}@{n.start:g}/{n.duration:g}" for n in notes))
    return (f"Earlier motif to echo (phrase #{number}; note@start/length in beats):\n  " + "\n  ".join(bits))
