#!/usr/bin/env python3
"""Ambient music agent: Claude (or a local model) composes slow phrases, played live as MIDI.

Mac -> USB MIDI interface -> synths / MIDI-to-CV modules, one monophonic voice per track.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import signal
import sys
import threading

import anthropic
import mido

from composer import (DEFAULT_MODEL, DEFAULT_OLLAMA_MODEL, ClaudeComposer, MockComposer,
                      OllamaComposer, Phrase, Settings, Track)
from feedback import FeedbackListener
from music import NOTE_NAMES, clean_phrase, format_phrase, note_name, parse_key, parse_note, scale_pitches
from player import DryRunPort, Player, find_port, output_names, save_midi


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Claude composes slow ambient phrases and plays them live as monophonic MIDI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--list-ports", action="store_true", help="list MIDI outputs and exit")
    p.add_argument("--port", help="MIDI output name, or a unique part of it (e.g. 'DIN')")
    p.add_argument("--channel", type=int, default=8, help="MIDI channel, 1-16 (single track)")
    p.add_argument("--track", action="append", metavar="CH[:NAME[:LOW-HIGH]]",
                   help="add a voice, e.g. 7:melody or 12:bass:C1-C3 (repeat for more; "
                        "range defaults to --low/--high). Replaces --channel")
    p.add_argument("--key", default="D dorian", help="root and mode, e.g. 'A aeolian', 'F lydian'")
    p.add_argument("--low", default="C2", help="lowest note (default range for every track)")
    p.add_argument("--high", default="C5", help="highest note (default range for every track)")
    p.add_argument("--bpm", type=float, default=60.0, help="tempo")
    p.add_argument("--beats", type=float, default=32.0, help="phrase length in beats")
    p.add_argument("--style", default="", help="mood hints for Claude, e.g. 'misty, sparse, hopeful'")
    p.add_argument("--continuous", action="store_true",
                   help="keep going: compose the next phrase while the current one plays")
    p.add_argument("--loop", action="store_true", dest="continuous", help=argparse.SUPPRESS)  # old name
    p.add_argument("--phrases", type=int, default=0, help="with --continuous, stop after this many (0 = forever)")
    p.add_argument("--dry-run", action="store_true", help="print notes instead of sending MIDI")
    p.add_argument("--mock", action="store_true", help="don't call the API; use a simple offline composer")
    p.add_argument("--ollama", nargs="?", const=DEFAULT_OLLAMA_MODEL, metavar="MODEL",
                   help=f"compose with a local model via Ollama instead of Claude (default {DEFAULT_OLLAMA_MODEL})")
    p.add_argument("--seed", type=int, help="random seed for --mock")
    p.add_argument("--save", metavar="FILE.mid", help="also write everything played to a MIDI file")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Claude model ID")
    p.add_argument("--feedback", metavar="PORT:CH:CC",
                   help="listen to a CC from the rack, e.g. DIN:15:3 (input port, channel, CC number)")
    p.add_argument("--peak", type=int, default=91,
                   help="with --feedback: a CC value at or above this triggers a one-phrase key change")
    p.add_argument("--peak-key", metavar="'ROOT MODE'",
                   help="key for the peak excursion (default: up a fifth, same mode)")
    p.add_argument("--peak-cooldown", type=int, default=3,
                   help="ignore new peaks for this many phrases after a key change")
    p.add_argument("--feedback-debug", action="store_true", help="print every feedback CC value received")
    args = p.parse_args(argv)

    if args.mock and args.ollama:
        p.error("use either --mock or --ollama, not both")
    if not 1 <= args.channel <= 16:
        p.error("--channel must be 1-16")
    if args.bpm <= 0 or args.beats <= 0:
        p.error("--bpm and --beats must be positive")
    return args


class Background:
    """Runs one composition on a daemon thread, so Ctrl+C never waits on the API."""

    def __init__(self, fn, *fn_args):
        self.result = None
        self.error = None
        self.thread = threading.Thread(target=self._run, args=(fn, *fn_args), daemon=True)
        self.thread.start()

    def _run(self, fn, *fn_args):
        try:
            self.result = fn(*fn_args)
        except BaseException as e:  # surfaced to the main thread in get()
            self.error = e

    def get(self):
        while self.thread.is_alive():
            self.thread.join(0.1)  # short joins keep Ctrl+C responsive
        if self.error:
            raise self.error
        return self.result


_NOTE = r"[A-Ga-g][#b]?-?\d+"
_TRACK = re.compile(rf"(\d+)(?::([A-Za-z][A-Za-z0-9_]*))?(?::({_NOTE})-({_NOTE}))?")


def key_name(root: int, mode: str) -> str:
    return f"{NOTE_NAMES[root]} {mode}"


def in_key(tracks: list[Track], root: int, mode: str) -> list[Track]:
    """The same tracks with their allowed notes recomputed for another key."""
    return [dataclasses.replace(t, scale=scale_pitches(root, mode, t.low, t.high)) for t in tracks]


def open_feedback(spec: str, peak: int, debug: bool) -> FeedbackListener:
    try:
        port, channel, cc = spec.rsplit(":", 2)
        channel, cc = int(channel), int(cc)
    except ValueError:
        raise SystemExit(f"Can't read --feedback {spec!r}. Use PORT:CHANNEL:CC, e.g. DIN:15:3")
    if not 1 <= channel <= 16 or not 0 <= cc <= 127:
        raise SystemExit("--feedback: channel must be 1-16 and CC 0-127")
    name = find_port(port, mido.get_input_names(), kind="input")
    print(f"Feedback: CC{cc} on channel {channel} from {name}; key change at >= {peak}")
    return FeedbackListener(name, channel, cc, peak=peak, debug=debug)


def build_tracks(args, root: int, mode: str) -> list[Track]:
    """Tracks from --track specs, or a single 'melody' track on --channel."""
    specs = args.track or [str(args.channel)]
    tracks = []
    for i, spec in enumerate(specs):
        m = _TRACK.fullmatch(spec.strip())
        if not m:
            raise SystemExit(f"Can't read --track {spec!r}. Use CH, CH:NAME or CH:NAME:LOW-HIGH, e.g. 12:bass:C1-C3")
        channel = int(m.group(1))
        name = m.group(2) or ("melody" if i == 0 else f"voice{i + 1}")
        low = parse_note(m.group(3) or args.low)
        high = parse_note(m.group(4) or args.high)
        if not 1 <= channel <= 16:
            raise SystemExit(f"Track {name!r}: channel must be 1-16")
        if name == "intent":
            raise SystemExit("'intent' is reserved - pick another track name")
        if low >= high:
            raise SystemExit(f"Track {name!r}: the low note must be below the high note")
        scale = scale_pitches(root, mode, low, high)
        if len(scale) < 3:
            raise SystemExit(f"Track {name!r}: that range holds fewer than 3 scale notes - widen it")
        tracks.append(Track(name, channel, low, high, scale))

    for attr in ("channel", "name"):
        values = [getattr(t, attr) for t in tracks]
        if len(values) != len(set(values)):
            raise SystemExit(f"Each track needs its own {attr}")
    return tracks


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.list_ports:
        names = output_names()
        print("MIDI outputs:" if names else "No MIDI outputs found.")
        for n in names:
            print(f"  {n}")
        return 0

    try:
        root, mode = parse_key(args.key)
        tracks = build_tracks(args, root, mode)
        peak_root, peak_mode = parse_key(args.peak_key) if args.peak_key else ((root + 7) % 12, mode)
    except ValueError as e:
        raise SystemExit(str(e))
    settings = Settings(root, mode, args.bpm, args.beats, args.style, tracks)
    home, excursion = key_name(root, mode), key_name(peak_root, peak_mode)
    excursion_settings = dataclasses.replace(
        settings, root=peak_root, mode=peak_mode, tracks=in_key(tracks, peak_root, peak_mode),
        note=(f"the modular rack just peaked, so this one phrase modulates to {excursion} "
              f"(home key is {home}). Make the shift feel deliberate - a lift or a shaft of light."),
    )
    return_settings = dataclasses.replace(
        settings, note=f"the previous phrase was a one-phrase excursion to {excursion}; settle back home into {home}.",
    )

    if args.dry_run:
        port, port_label = DryRunPort(), "dry run (printing only)"
    else:
        if not args.port:
            raise SystemExit("Pick an output with --port (see --list-ports), or use --dry-run.")
        port_label = find_port(args.port)
        port = mido.open_output(port_label)

    if not (args.mock or args.ollama) and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        port.close()
        raise SystemExit("ANTHROPIC_API_KEY isn't set. Export it (see README), or use --mock.")
    if args.mock:
        composer, composer_label = MockComposer(args.seed), "mock"
    elif args.ollama:
        composer, composer_label = OllamaComposer(args.ollama), f"{args.ollama} (local, via Ollama)"
    else:
        composer, composer_label = ClaudeComposer(args.model), args.model
    player = Player(port, channels=[t.channel - 1 for t in tracks], bpm=args.bpm)
    played: list[Phrase] = []

    # Treat SIGTERM like Ctrl+C so the cleanup below still runs.
    def on_sigterm(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_sigterm)

    print(f"Output: {port_label}")
    for t in tracks:
        print(f"  {t.name}: channel {t.channel}, {note_name(t.low)}-{note_name(t.high)}")
    print(f"{args.key}, {args.bpm:g} BPM, {args.beats:g}-beat phrases"
          + (f", style: {args.style}" if args.style else ""))
    print(f"Composer: {composer_label}. Ctrl+C to stop.")
    feedback = open_feedback(args.feedback, args.peak, args.feedback_debug) if args.feedback else None
    print()

    def compose(previous: Phrase | None, s: Settings) -> Phrase:
        phrase = composer.compose(s, previous)
        for t in s.tracks:
            phrase.parts[t.name] = clean_phrase(phrase.parts.get(t.name, []), s.beats, t.scale)
        return phrase

    cooldown = 0

    def next_settings(n_next: int, current_is_excursion: bool) -> Settings:
        """Pick the key for the phrase about to be composed, from what the rack did last phrase."""
        nonlocal cooldown
        if feedback is None:
            return settings
        window = feedback.take_window()
        print(f"  [feedback] last phrase: {window.describe()}")
        cooldown = max(0, cooldown - 1)
        if current_is_excursion:
            print(f"  [key] phrase {n_next} returns home to {home}")
            return return_settings
        if window.high is not None and window.high >= args.peak:
            if cooldown:
                print(f"  [key] peak {window.high} ignored - cooldown, {cooldown} more phrase(s)")
                return settings
            cooldown = args.peak_cooldown + 1
            print(f"  [key] peak {window.high} >= {args.peak}: phrase {n_next} will be in {excursion}")
            return excursion_settings
        return settings

    def channel_parts(phrase: Phrase) -> list[tuple[int, list]]:
        return [(t.channel - 1, phrase.parts[t.name]) for t in tracks]

    try:
        print("Composing...")
        current = Background(compose, None, settings).get()
        current_settings = settings
        n = 1
        while True:
            key_tag = f" [{excursion}]" if current_settings is excursion_settings else ""
            print(f"\nPhrase {n}{key_tag}: {current.intent}")
            for t in tracks:
                if len(tracks) > 1:
                    print(f"  {t.name} (ch {t.channel}):")
                print(format_phrase(current.parts[t.name]))
            more = args.continuous and (args.phrases == 0 or n < args.phrases)
            upcoming = upcoming_settings = None
            if more:
                upcoming_settings = next_settings(n + 1, current_settings is excursion_settings)
                upcoming = Background(compose, current, upcoming_settings)
            player.play(channel_parts(current), settings.beats)
            played.append(current)
            if upcoming is None:
                break
            if upcoming.thread.is_alive():
                print("(still composing the next phrase...)")
            current, current_settings = upcoming.get(), upcoming_settings
            n += 1
    except KeyboardInterrupt:
        print("\nStopping.")
    except anthropic.AuthenticationError:
        print("\nAPI authentication failed - is ANTHROPIC_API_KEY set? (or use --mock)", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("\nCouldn't reach the Anthropic API - check your connection.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"\nAPI error {e.status_code}: {e.message}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"\n{e}", file=sys.stderr)
        return 1
    finally:
        player.panic()
        port.close()
        if feedback:
            feedback.close()
        if args.save and played:
            save_midi(args.save, [channel_parts(p) for p in played], settings.beats, args.bpm)
            print(f"Saved {len(played)} phrase(s) to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
