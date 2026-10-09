"""CV pitch mode: the composer's melody played as 1 V/oct voltages straight out of the ES-8, no MIDI.

Each voice has an ES-8 output (pitch CV to an oscillator) and an ES-8 input (that voice's Contour 1 envelope). The
rack decides WHEN a note sounds (its triggers fire the Contour); the agent decides WHICH note. A voice holds its
pitch while its envelope runs and steps to the next note of the melody only after the envelope has fully fallen,
so a pitch never changes under a sounding note.

0 V is the root of the scale (tune the oscillator so 0 V sounds that note). The home octave is 0 to +1 V; notes may
go one octave below and one above it (-1 V to just under +2 V).
"""

from __future__ import annotations

import signal
import threading
import time
from collections import deque

from composer import ClaudeComposer, MockComposer, OllamaComposer, Phrase, Settings, Track
from envelope_log import Tracker
from es8 import ES8, FULL_SCALE_VOLTS
from music import clean_phrase, note_name, parse_key, parse_note, scale_pitches, use_flats

STEP_SLEW = 500.0   # volts per second: a pitch step finishes within one 21 ms block
REFILL_BELOW = 4    # compose the next phrase when any voice has fewer notes than this left
POLL_SECONDS = 0.05

NOTE_FOR_RACK = ("the rack, not you, decides when each note sounds, so only the ORDER of pitches matters. Rhythm, "
                 "note lengths and rests are ignored: write each voice as a patient melodic line that is good heard "
                 "one note at a time, a few seconds apart. Use the whole allowed range, three octaves, not one: mix "
                 "small steps with leaps of a fourth, a fifth or an octave, and a clear shape across the phrase. "
                 "Never return to a pitch within the voice's last 4 notes, and avoid the same note name (in any "
                 "octave) twice in a row. Each voice should have its own register, but let them cross sometimes.")
REPEAT_PITCHES = 4  # a voice may not replay one of its last N pitches ...
REPEAT_NAMES = 2    # ... or the same note name (any octave) as one of its last N notes


def avoid_recent(pitches: list[int], scale: list[int], history: list[int]) -> list[int]:
    """Replace any pitch the voice played too recently with the nearest scale note it didn't. Octave moves count,
    so a repeat can come back in another octave. `history` is what the voice already has queued or has played."""
    hist = list(history)
    out = []
    for p in pitches:
        recent = hist[-REPEAT_PITCHES:]
        names = {q % 12 for q in hist[-REPEAT_NAMES:]}
        if p in recent or p % 12 in names:
            options = [q for q in scale if q not in recent and q % 12 not in names]
            if options:
                p = min(options, key=lambda q: (abs(q - p), q))
        out.append(p)
        hist.append(p)
    return out


def parse_ints(text: str) -> list[int]:
    return [int(p) for p in text.split(",") if p.strip()]


class CVVoice:
    """One voice: a queue of pitches, one ES-8 output, one envelope input."""

    def __init__(self, name: str, es8: ES8, out_jack: int, zero_pitch: int):
        self.name = name
        self.es8 = es8
        self.out_jack = out_jack
        self.zero_pitch = zero_pitch
        self.queue: deque[int] = deque()
        self.recent: list[int] = []   # the last pitches queued for this voice, oldest first (for avoid_recent)
        self.pitch: int | None = None
        self.steps = 0

    def volts(self, pitch: int) -> float:
        return (pitch - self.zero_pitch) / 12.0

    def advance(self, why: str) -> bool:
        """Step to the next queued pitch. Returns False (and holds the current pitch) if none is ready."""
        if not self.queue:
            print(f"  [{self.name}] {why}: no new note ready, holding {note_name(self.pitch) if self.pitch else '0 V'}")
            return False
        self.pitch = self.queue.popleft()
        sent = self.es8.set_cv(self.out_jack, self.volts(self.pitch))
        self.steps += 1
        print(f"  [{self.name}] {why}: next note {note_name(self.pitch)} -> out {self.out_jack} {sent:+.3f} V "
              f"({len(self.queue)} queued)")
        return True


def run_cv_pitch(args, compose_with_fallback) -> int:
    use_flats(args.cv_key.strip()[1:2] == "b")
    try:
        root, mode = parse_key(args.cv_key)
        zero = parse_note(args.cv_zero)
    except ValueError as e:
        raise SystemExit(str(e))
    if zero % 12 != root:
        raise SystemExit(f"--cv-zero {args.cv_zero} must be the root of --cv-key {args.cv_key!r}, so 0 V is the root")
    outs, envs = parse_ints(args.cv_out), parse_ints(args.cv_env)
    if not outs or len(outs) != len(envs):
        raise SystemExit("--cv-out and --cv-env need the same number of jacks, one pair per voice, e.g. 1,2 and 1,2")

    low, high = zero - 12, zero + 23   # one octave below the home octave, one above it
    scale = scale_pitches(root, mode, low, high)
    names = [f"voice{i + 1}" for i in range(len(outs))]
    tracks = [Track(n, 8 + i, low, high, scale) for i, n in enumerate(names)]
    settings = Settings(root, mode, args.bpm, args.beats, args.style, tracks, note=NOTE_FOR_RACK)

    if not (args.mock or args.ollama):
        import os
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise SystemExit("ANTHROPIC_API_KEY isn't set. Export it (see README), or use --mock.")
    if args.mock:
        composer, label = MockComposer(args.seed), "mock"
    elif args.ollama:
        composer, label = OllamaComposer(args.ollama), f"{args.ollama} (local, via Ollama)"
    else:
        composer, label = ClaudeComposer(args.model, args.effort), f"{args.model} (effort {args.effort})"
    offline = MockComposer(args.seed)

    free = [j for j in range(1, 9) if j not in outs]
    es8 = ES8(in_channels=envs, out_channel=free[-1], level=0.0, dry_run=args.dry_run,
              cv_channels=tuple(outs), cv_slew=STEP_SLEW)
    voices = [CVVoice(n, es8, o, zero) for n, o in zip(names, outs)]
    trackers = [Tracker(n) for n in names]

    print(f"{'DRY RUN: reading the envelopes, sending no voltage' if args.dry_run else 'Sending pitch CV'} "
          f"via {es8.device_name}")
    for v, e in zip(voices, envs):
        print(f"  {v.name}: pitch out {v.out_jack}, envelope in {e}")
    print(f"{args.cv_key}, 0 V = {note_name(zero)}, notes {note_name(low)} to {note_name(high)} "
          f"({(low - zero) / 12:+.0f} V to {(high - zero) / 12:+.2f} V)")
    print(f"Composer: {label}. Ctrl+C to stop.\n")

    def on_sigterm(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_sigterm)

    lock = threading.Lock()
    state = {"previous": None, "composing": False, "phrases": 0}

    def compose_next():
        try:
            started = time.monotonic()
            with lock:
                previous = state["previous"]
            phrase: Phrase = compose_with_fallback(composer, offline, settings, previous, label="melody")
            for t in tracks:
                phrase.parts[t.name] = clean_phrase(phrase.parts.get(t.name, []), settings.beats, t.scale)
            with lock:
                state["previous"] = phrase
                state["phrases"] += 1
                for v in voices:
                    notes = sorted(phrase.parts.get(v.name, []), key=lambda n: n.start)
                    pitches = avoid_recent([n.pitch for n in notes], scale, v.recent)
                    v.queue.extend(pitches)
                    v.recent = (v.recent + pitches)[-REPEAT_PITCHES:]
                counts = ", ".join(f"{v.name} {len(v.queue)}" for v in voices)
            print(f"  [melody {state['phrases']}] {phrase.intent} ({time.monotonic() - started:.1f} s; "
                  f"notes queued: {counts})")
        except Exception as e:  # keep the session alive; the voices just hold their pitch
            print(f"  [melody] composing failed: {type(e).__name__}: {e}")
        finally:
            state["composing"] = False

    def maybe_refill():
        with lock:
            if state["composing"] or min(len(v.queue) for v in voices) >= REFILL_BELOW:
                return
            state["composing"] = True
        threading.Thread(target=compose_next, daemon=True).start()

    try:
        maybe_refill()
        while not all(v.queue for v in voices):
            time.sleep(0.1)          # wait for the first melody before the first note is set
            maybe_refill()
        for v in voices:
            v.advance("start")
        while True:
            time.sleep(POLL_SECONDS)
            for t, means, _peaks in es8.drain():
                for i, (v, tr) in enumerate(zip(voices, trackers)):
                    done = tr.feed(t, float(means[i]) * FULL_SCALE_VOLTS, 0.0)
                    if done:
                        why = f"envelope done (rise {done['rise']:.1f}s, fall {done['fall']:.1f}s, peak {done['peak']:.1f}V)"
                        with lock:
                            v.advance(why)
            maybe_refill()
            if args.cv_notes and all(v.steps > args.cv_notes for v in voices):
                print(f"\nPlayed {args.cv_notes} notes per voice. Stopping.")
                break
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        es8.close()
        print("ES-8 outputs back to 0 V.")
    return 0
