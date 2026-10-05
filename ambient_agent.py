#!/usr/bin/env python3
"""Ambient music agent: Claude (or a local model) composes slow phrases, played live as MIDI.

Mac -> USB MIDI interface -> synths / MIDI-to-CV modules, one monophonic voice per track.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import random
import re
import signal
import sys
import threading
import time

import anthropic
import mido

from composer import (DEFAULT_MODEL, DEFAULT_OLLAMA_MODEL, ClaudeComposer, MockComposer,
                      OllamaComposer, Phrase, Settings, Track)
from feedback import CombinedFeedback, FeedbackListener
from form import (FormPlanner, PhraseSummary, anchor_text, history_text, novelty, similarity, snap_to_rhythm,
                  summarize, window_tracks)
from music import clean_phrase, format_phrase, note_name, parse_key, parse_note, pc_name, scale_pitches, use_flats
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
    p.add_argument("--seed", type=int, help="random seed for --mock and the form planner")
    p.add_argument("--form", choices=["on", "off"], default="on",
                   help="plan sections (statement, contrast, space, return) with register windows and a memory of "
                        "recent phrases, to keep long sessions from settling into a rut; 'off' only ever "
                        "develops the previous phrase")
    p.add_argument("--memory", type=int, default=8, metavar="N",
                   help="with --form on: how many recent phrases the model is shown (summaries)")
    p.add_argument("--retry-similar", type=float, default=0.0, metavar="X",
                   help="if a phrase's similarity to a recent one is above X (0-1), compose it once more "
                        "(0 = never; costs an extra call, so keep the tempo slow enough)")
    p.add_argument("--save", metavar="FILE.mid", help="also write everything played to a MIDI file")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Claude model ID")
    p.add_argument("--feedback", metavar="PORT:CH:CC",
                   help="listen to a CC from the rack, e.g. DIN:15:3 (input port, channel, CC number)")
    p.add_argument("--peak", default="auto", metavar="N|auto",
                   help="with --feedback: what counts as a peak (pulses --peak-out). A CC value (e.g. 91) at or "
                        "above it, or 'auto': the top of the CC's own range over the last minute, which also "
                        "works when the envelope idles high")
    p.add_argument("--key-change", action="store_true",
                   help="with --feedback: also move to --peak-key for one phrase after a peak "
                        "(off by default - leave key changes to the rack's quantizers)")
    p.add_argument("--peak-key", metavar="'ROOT MODE'",
                   help="with --key-change: key for the excursion (default: up a fifth, same mode)")
    p.add_argument("--peak-cooldown", type=int, default=3,
                   help="with --key-change: ignore new peaks for this many phrases after a key change")
    p.add_argument("--peak-out", metavar="CH:CC[:LEVEL]", default="15:20:64",
                   help="with --feedback: on each peak, pulse this CC to LEVEL (1-127, then back to 0) out --port "
                        "for the rack; in VCV's MIDI CC->CV, 64 is about 5 V. 'off' to disable")
    p.add_argument("--feedback-debug", action="store_true", help="print every feedback value received")
    p.add_argument("--es8", action="store_true",
                   help="use an Expert Sleepers ES-8 directly instead of the Hapax/VCV route: read the envelope "
                        "follower (and a second input) and send peak triggers out of an ES-8 output")
    p.add_argument("--es8-follower", type=int, default=0, metavar="N",
                   help="with --es8: the ES-8 input carrying an envelope follower of the music; peaks are found in "
                        "it and send a trigger out (0 = none, the default)")
    p.add_argument("--es8-trig", default="1,2", metavar="N[,N...]",
                   help="with --es8: the ES-8 inputs carrying each voice's trigger as it actually fired (e.g. out of "
                        "a lockout), in --track order; the agent learns which of its notes sounded (0 = none)")
    p.add_argument("--es8-walk", default="3,4", metavar="N[,N...]",
                   help="with --es8: ES-8 inputs summarized each phrase, e.g. random walks: '3,4' (0 = none)")
    p.add_argument("--es8-out", default="1:0.5", metavar="CH[:LEVEL]",
                   help="with --es8: the ES-8 output that gets the 100 ms peak trigger, and its level as a fraction "
                        "of full scale (0.5 is about 5 V if +-1.0 is about +-10 V)")
    p.add_argument("--shift-on", default="", metavar="SECTIONS",
                   help="with --es8 and --form on: send the trigger out --es8-out when one of these sections "
                        "begins and again when it ends, e.g. 'contrast' (so contrast phrases sit a fifth up if the "
                        "trigger steps your shift); sections: statement, contrast, space, return")
    p.add_argument("--peak-gap", type=float, default=0.0, metavar="SECONDS",
                   help="with --feedback or --es8: at least this long between peak triggers (0 = no limit)")
    p.add_argument("--rack-state", choices=["on", "off"], default="on",
                   help="with --es8: tell the model how active the rack was and where the walk is, as a gentle nudge")
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
    return f"{pc_name(root)} {mode}"


def in_key(tracks: list[Track], root: int, mode: str) -> list[Track]:
    """The same tracks with their allowed notes recomputed for another key."""
    return [dataclasses.replace(t, scale=scale_pitches(root, mode, t.low, t.high)) for t in tracks]


PULSE_SECONDS = 0.1


def parse_peak_out(spec: str) -> tuple[int, int, int] | None:
    """'15:20:64' -> (14, 20, 64): 0-based channel, CC number, pulse level; None for 'off'."""
    if spec.lower() == "off":
        return None
    try:
        parts = [int(x) for x in spec.split(":")]
        channel, cc = parts[0], parts[1]
        level = parts[2] if len(parts) == 3 else 127
        if len(parts) > 3:
            raise ValueError
    except (ValueError, IndexError):
        raise SystemExit(f"Can't read --peak-out {spec!r}. Use CH:CC or CH:CC:LEVEL, e.g. 15:20:64, or 'off'")
    if not 1 <= channel <= 16 or not 0 <= cc <= 127 or not 1 <= level <= 127:
        raise SystemExit("--peak-out: channel must be 1-16, CC 0-127, level 1-127")
    return channel - 1, cc, level


def parse_peak(value: str) -> int | str:
    """'auto' stays 'auto'; anything else must be a CC value 0-127."""
    if value.lower() == "auto":
        return "auto"
    try:
        n = int(value)
    except ValueError:
        raise SystemExit(f"--peak must be a number 0-127 or 'auto', got {value!r}")
    if not 0 <= n <= 127:
        raise SystemExit("--peak must be 0-127 or 'auto'")
    return n


def parse_es8_out(spec: str) -> tuple[int, float]:
    """'1:0.5' -> (1, 0.5): ES-8 output number and pulse level (fraction of full scale)."""
    try:
        parts = spec.split(":")
        channel = int(parts[0])
        level = float(parts[1]) if len(parts) > 1 else 0.5
        if len(parts) > 2:
            raise ValueError
    except (ValueError, IndexError):
        raise SystemExit(f"Can't read --es8-out {spec!r}. Use CH or CH:LEVEL, e.g. 1:0.5")
    if channel < 1 or not 0 < level <= 1:
        raise SystemExit("--es8-out: channel must be 1 or more and LEVEL between 0 and 1")
    return channel, level


def open_feedback(spec: str, peak: int | str, debug: bool, key_change: bool, on_peak=None,
                  min_gap: float = 0.0) -> FeedbackListener:
    try:
        port, channel, cc = spec.rsplit(":", 2)
        channel, cc = int(channel), int(cc)
    except ValueError:
        raise SystemExit(f"Can't read --feedback {spec!r}. Use PORT:CHANNEL:CC, e.g. DIN:15:3")
    if not 1 <= channel <= 16 or not 0 <= cc <= 127:
        raise SystemExit("--feedback: channel must be 1-16 and CC 0-127")
    name = find_port(port, mido.get_input_names(), kind="input")
    print(f"Feedback: CC{cc} on channel {channel} from {name}; "
          + ("peaks found automatically (top of the last minute's range)" if peak == "auto" else f"peak at >= {peak}")
          + ("; one-phrase key change on peaks" if key_change else "; key changes off"))
    return FeedbackListener(name, channel, cc, peak=peak, debug=debug, on_peak=on_peak, min_gap=min_gap)


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

    # Spell notes the way the key was written: 'Bb ...' -> flats, 'A# ...' -> sharps.
    use_flats(args.key.strip()[1:2] == "b")
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

    shift_sections = {x.strip() for x in args.shift_on.split(",") if x.strip()}
    if shift_sections:
        if not shift_sections <= {"statement", "contrast", "space", "return"}:
            raise SystemExit("--shift-on takes section names: statement, contrast, space, return")
        if not args.es8 or args.form != "on":
            raise SystemExit("--shift-on needs --es8 and --form on")

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
    parse_peak(args.peak)
    peak_out = parse_peak_out(args.peak_out)  # validate even when unused, so typos surface
    if not args.feedback or args.es8:    # with --es8 the peak trigger goes out an ES-8 output, not as a CC
        peak_out = None

    def pulse_peak_out(value: int):
        """Tell the rack a peak happened: CC high now, back to 0 shortly after."""
        out_channel, out_cc, level = peak_out
        player.send_cc(out_channel, out_cc, level)
        threading.Timer(PULSE_SECONDS, player.send_cc, (out_channel, out_cc, 0)).start()
        print(f"  [feedback] -> pulsed CC{out_cc} on channel {out_channel + 1}")

    feedback = None
    es8 = None
    midi_feedback = None
    if args.es8:
        if args.feedback and args.es8_follower:
            raise SystemExit("--feedback already supplies the follower; use --es8-follower 0 with it")
        from es8 import ES8, ES8Feedback  # imported here so sounddevice is only needed with --es8
        out_ch, out_level = parse_es8_out(args.es8_out)
        def parse_inputs(text: str, flag: str) -> list[int]:
            try:
                return [int(x) for x in text.split(",") if x.strip() not in ("", "0")]
            except ValueError:
                raise SystemExit(f"{flag} must be input numbers like 1,2 (or 0), got {text!r}")

        walks = parse_inputs(args.es8_walk, "--es8-walk")
        trig_inputs = parse_inputs(args.es8_trig, "--es8-trig")
        if len(trig_inputs) > len(tracks):
            raise SystemExit(f"--es8-trig lists {len(trig_inputs)} inputs but there are only {len(tracks)} tracks")
        trig_map = {t.name: n for t, n in zip(tracks, trig_inputs)}   # voice name -> input, in track order
        follower = args.es8_follower or None
        inputs = ([follower] if follower else []) + trig_inputs + walks
        if not inputs:
            raise SystemExit("--es8 needs at least one input: --es8-trig, --es8-walk or --es8-follower")
        if len(set(inputs)) != len(inputs):
            raise SystemExit("--es8-follower, --es8-trig and --es8-walk must use different inputs")
        es8 = ES8(inputs, out_ch, out_level, dry_run=args.dry_run)

        def pulse_es8(value: int):
            es8.pulse()
            print(f"  [feedback] -> trigger on ES-8 output {out_ch}" + (" (dry run: not sent)" if args.dry_run else ""))

        es8_feedback = ES8Feedback(es8, parse_peak(args.peak), args.feedback_debug, on_peak=pulse_es8,
                                   min_gap=args.peak_gap, rack_hint=args.rack_state == "on",
                                   follower=follower, walks=walks, triggers=trig_map)
        feedback = es8_feedback
        by_channel = {t.channel - 1: t.name for t in tracks}
        player.on_note = lambda ch, when: es8_feedback.note_sent(by_channel.get(ch, ""), when)
        listed = ([f"input {follower} = envelope follower"] if follower else [])
        listed += [f"input {n} = {name} trigger" for name, n in trig_map.items()]
        listed += ([f"input{'s' if len(walks) > 1 else ''} {','.join(map(str, walks))} = walk"] if walks else [])
        print(f"ES-8 ({es8.device_name}, {es8.rate} Hz): " + ", ".join(listed)
              + f"; output {out_ch} gets a 100 ms trigger at {out_level:g} of full scale "
                f"(~{out_level * 10:.1f} V if +-1.0 is +-10 V)")
    if args.feedback:    # the MIDI route (e.g. a Hapax sending a CC): alone, or together with --es8
        on_peak = pulse_es8 if es8 else (pulse_peak_out if peak_out else None)
        midi_feedback = open_feedback(args.feedback, parse_peak(args.peak), args.feedback_debug, args.key_change,
                                      on_peak=on_peak, min_gap=args.peak_gap)
        feedback = CombinedFeedback(midi_feedback, es8_feedback) if es8 else midi_feedback
    if peak_out:
        print(f"Peak out: CC{peak_out[1]} pulse to {peak_out[2]} (~{peak_out[2] * 10 / 127:.1f} V in VCV) "
              f"on channel {peak_out[0] + 1} via {port_label}")
    print()

    def compose(previous: Phrase | None, s: Settings, recent: list, label: str = "phrase") -> Phrase:
        def clean(phrase: Phrase) -> Phrase:
            for t in s.tracks:
                notes = phrase.parts.get(t.name, [])
                if s.rhythm is not None:
                    notes = snap_to_rhythm(notes, s.rhythm.get(t.name, []), s.beats)
                phrase.parts[t.name] = clean_phrase(notes, s.beats, t.scale)
            return phrase

        phrase = clean(composer.compose(s, previous))
        if args.retry_similar and recent:
            worst = max(similarity(summarize(0, "", phrase.parts), r) for r in recent[-4:])
            if worst > args.retry_similar:
                print(f"  [form] {label} too similar to a recent phrase ({worst:.2f}) - composing it again")
                again = dataclasses.replace(
                    s, note=(s.note + " " if s.note else "")
                    + "Your first attempt was too similar to a recent phrase. Write something clearly different: "
                      "a new rhythm, a new contour, a different number of notes.")
                phrase = clean(composer.compose(again, previous))
        return phrase

    # Form: where we are in the larger shape, a memory of recent phrases, and the motif a RETURN echoes.
    planner = FormPlanner([t.name for t in tracks], settings.beats, random.Random(args.seed)) if args.form == "on" else None
    history: list[PhraseSummary] = []
    novelties: list[float] = []
    anchor = None  # (phrase number, parts) of the latest "statement 1" phrase

    def plan_next(base: Settings, current: Phrase | None):
        """Settings for the phrase about to be composed, the previous phrase to show (if any), and its plan."""
        if rack_hint:
            base = dataclasses.replace(base, note=(base.note + " " if base.note else "") + rack_hint)
        if planner is None:
            return base, current, None
        plan = planner.next(history)
        text = history_text(history, args.memory)
        if plan.echo_anchor and anchor:
            text = (text + "\n" if text else "") + anchor_text(*anchor)
        shaped = dataclasses.replace(
            base, tracks=window_tracks(base.tracks, plan.registers),
            note=(base.note + " " if base.note else "") + plan.instruction, history=text, rhythm=plan.rhythm)
        return shaped, (current if plan.develop else None), plan

    cooldown = 0
    shifted = False   # whether the rack's shift is currently stepped up (assumes each trigger steps it)
    rack_hint = ""  # what the rack did last phrase, as a sentence for the model (--es8 with --rack-state on)

    def next_settings(n_next: int, current_is_excursion: bool) -> Settings:
        """Pick the key for the phrase about to be composed, from what the rack did last phrase."""
        nonlocal cooldown, rack_hint
        if feedback is None:
            return settings
        window = feedback.take_window()
        rack_hint = window.hint
        print(f"  [feedback] last phrase: {window.describe()}")
        if not args.key_change:
            return settings
        cooldown = max(0, cooldown - 1)
        if current_is_excursion:
            print(f"  [key] phrase {n_next} returns home to {home}")
            return return_settings
        if window.peaks:
            if cooldown:
                print(f"  [key] peak ignored - cooldown, {cooldown} more phrase(s)")
                return settings
            cooldown = args.peak_cooldown + 1
            print(f"  [key] peak (max {window.high}): phrase {n_next} will be in {excursion}")
            return excursion_settings
        return settings

    def channel_parts(phrase: Phrase) -> list[tuple[int, list]]:
        return [(t.channel - 1, phrase.parts[t.name]) for t in tracks]

    try:
        print("Composing...")
        first_settings, first_previous, current_plan = plan_next(settings, None)
        current = Background(compose, first_previous, first_settings, [], "phrase 1").get()
        current_settings = settings
        n = 1
        while True:
            key_tag = f" [{excursion}]" if current_settings is excursion_settings else ""
            plan_tag = f" [{current_plan.label()}]" if current_plan else ""
            summary = summarize(n, current_plan.section if current_plan else "-", current.parts)
            print(f"\nPhrase {n}{key_tag}{plan_tag}: {current.intent}")
            if history:
                score = novelty(summary, history)
                novelties.append(score)
                print(f"  novelty {score:.2f} vs the last {min(4, len(history))} (1.00 = brand new)")
            if current_plan and current_plan.section == "statement" and current_plan.step == 1:
                anchor = (n, current.parts)
            history.append(summary)
            for t in tracks:
                if len(tracks) > 1:
                    print(f"  {t.name} (ch {t.channel}):")
                print(format_phrase(current.parts[t.name]))
            more = args.continuous and (args.phrases == 0 or n < args.phrases)
            upcoming = upcoming_settings = upcoming_plan = None
            if more:
                upcoming_settings = next_settings(n + 1, current_settings is excursion_settings)
                shaped, previous_shown, upcoming_plan = plan_next(upcoming_settings, current)
                upcoming = Background(compose, previous_shown, shaped, list(history), f"phrase {n + 1}")
            if shift_sections and current_plan:
                want = current_plan.section in shift_sections
                if want != shifted:
                    shifted = want
                    es8.pulse()
                    print(f"  [shift] {current_plan.section} {'begins' if want else 'is over'}: trigger on ES-8 "
                          f"output {out_ch}" + (" (dry run: not sent)" if args.dry_run else ""))
            player.play(channel_parts(current), settings.beats)
            played.append(current)
            if upcoming is None:
                break
            if upcoming.thread.is_alive():
                print("(still composing the next phrase...)")
            current, current_settings, current_plan = upcoming.get(), upcoming_settings, upcoming_plan
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
        if novelties:
            print(f"Average novelty over {len(novelties)} phrase(s): {sum(novelties) / len(novelties):.2f} "
                  f"(1.00 = every phrase brand new, 0.00 = identical)")
        if feedback:
            feedback.close()
        if es8:
            es8.close()
        if peak_out:
            time.sleep(PULSE_SECONDS)  # let an in-flight pulse finish before closing the port
            player.send_cc(peak_out[0], peak_out[1], 0)  # never leave the pulse high
        player.panic()
        port.close()
        if args.save and played:
            save_midi(args.save, [channel_parts(p) for p in played], settings.beats, args.bpm)
            print(f"Saved {len(played)} phrase(s) to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
