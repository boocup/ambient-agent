"""AI "chapter" conductor for the Morph 4 levels. By default a dry run: prints and logs only, sends NOTHING to the ES-8.
With --live it sends each chapter's four levels as 0-5 V from four consecutive ES-8 outputs (default 1-4, to the Morph 4
level CV inputs A-D), gliding over the chapter's ramp time, and waits out each chapter in real time.

Four voices go into the Joranalogue Morph 4. Every chapter (a minute or three) the model picks a level for each voice, how
long the chapter lasts and how slowly to ramp there. Levels are 0-1 (later 0-5 V on the Morph 4 level CVs).

    python chapters.py --backend ollama            # local model (qwen2.5:7b), free
    python chapters.py --backend mock              # no model, random chapters (baseline to compare against)
    python chapters.py --backend claude            # hosted Claude: costs a few cents, ask first
    python chapters.py --live --time-scale 0.25    # real voltage; chapters 4x shorter than written (a first listen)
    python chapters.py --backend claude --write-grid-dir DIR   # also write a CV Matrix grid (see write_grid), named by date, time and chapter 1

Each run writes chapters_log.jsonl (one line per chapter) and prints a summary. Read the chapters and ask: would this
sound musical? Nothing here touches the rack.
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
VOICES = ["env1", "env2", "piano", "drone"]
LOG = Path(__file__).with_name("chapters_log.jsonl")

SYSTEM = """You conduct slow ambient music by setting the level of four voices that feed a mixer.
The voices (levels 0.0 = silent to 1.0 = full):
- env1: a melodic voice with slow, long, evolving envelopes. Part of the main melodic bed.
- env2: a second melodic voice of the same kind, with different timing. Plays with env1 as a pair.
- piano: a sparse, random, soft piano-like voice. Plays single quiet notes at irregular times. Sparkle: too loud and it covers the others, silent for a long time and the music feels empty.
- drone: a steady pulsing drone on one repeated note. It anchors the mood but tires the ear if it stays loud for many chapters.
Write the next chapter. Rules: chapters last 1 to 4 minutes; ramps take 10 to 90 seconds and are at most half the chapter; change at most two voices by a lot at once; never silence all four; do not repeat the previous chapter; use the whole range, with real silence and real fullness for contrast, not only middle values. Keep the mood calm and slow."""


# Used instead of SYSTEM when the performer gives their own description of the voices (--brief) or a voice count other than four.
CUSTOM_SYSTEM = """You conduct slow ambient music by setting the level of {n} voice{plural} that feed a mixer. Levels run from 0.0 (silent) to 1.0 (full). The voice{plural} {are} called {names}, in that order.
The performer describes the setup and what they want, in their own words:
---
{brief}
---
Write the next chapter. Rules: chapters last 1 to 4 minutes; ramps take 10 to 90 seconds and are at most half the chapter; change at most two voices by a lot at once (with a single voice, change it by a moderate amount); never silence every voice; do not repeat the previous chapter; use the whole range, with real silence and real fullness for contrast, not only middle values. Keep the mood calm and slow unless the performer asks for something else."""


def custom_system(brief: str) -> str:
    n = len(VOICES)
    return CUSTOM_SYSTEM.format(n=n, plural="" if n == 1 else "s", are="is" if n == 1 else "are", names=", ".join(VOICES),
                                brief=brief or "(no description given: treat the voices as equal and unspecified)")


# Chapters written by hosted Claude on 2026-10-13 (a dry run), used as a worked example for small models.
EXAMPLE = [
    ("Waking Stillness", (0.45, 0.00, 0.12, 0.30), "A quiet opening where one melodic voice rises over a soft drone, with the second voice held back for later contrast."),
    ("Opening Bloom", (0.70, 0.55, 0.10, 0.15), "Bringing in the second melodic voice makes a fuller pair while the drone eases back to rest the ear."),
    ("Hollow Drift", (0.20, 0.85, 0.35, 0.00), "After the bloom, the drone drops to true silence and env1 recedes while env2 takes the lead, with piano gently stepping forward for contrast."),
    ("Quiet Descent", (0.00, 0.40, 0.50, 0.45), "After the fullness of env2, env1 falls to true silence while the drone returns to anchor and piano sparkles through the open space."),
    ("Rising Fullness", (0.90, 0.60, 0.08, 0.25), "After the sparse descent, the melodic pair swells to full while the piano recedes and the drone eases off."),
    ("Ebbing Light", (0.12, 0.00, 0.30, 0.10), "After the full bloom, the melodic voices recede and let the sparse piano glow in the open space."),
]


def example_text() -> str:
    lines = [f"{i + 1}. {n}: " + ", ".join(f"{v} {x:.2f}" for v, x in zip(VOICES, lv)) + f" - {why}"
             for i, (n, lv, why) in enumerate(EXAMPLE)]
    return ("\n\nHere is an example of a good opening, to show the range and the contrast. Do NOT copy it: write new "
            "chapters with their own names and shapes, and make real use of 0.00 and of high levels.\n" + "\n".join(lines))


def schema() -> dict:
    level = {"type": "number", "minimum": 0, "maximum": 1}
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "two or three words naming the chapter's mood"},
            "levels": {"type": "object", "properties": {v: level for v in VOICES}, "required": VOICES,
                       "additionalProperties": False},
            "minutes": {"type": "number", "minimum": 1, "maximum": 4},
            "ramp_seconds": {"type": "number", "minimum": 10, "maximum": 90},
            "why": {"type": "string", "description": "one short sentence"},
        },
        "required": ["name", "levels", "minutes", "ramp_seconds", "why"],
        "additionalProperties": False,
    }


def user_prompt(history: list[dict], elapsed_min: float) -> str:
    if not history:
        return "This is the opening chapter. The piece has just begun. Write chapter 1."
    lines = [f"{i + 1}. {c['name']}: " + ", ".join(f"{v} {c['levels'][v]:.2f}" for v in VOICES)
             + f" ({c['minutes']:.1f} min)" for i, c in enumerate(history[-6:], start=max(0, len(history) - 6))]
    return (f"The piece has been playing for {elapsed_min:.0f} minutes. Recent chapters:\n" + "\n".join(lines)
            + f"\nWrite chapter {len(history) + 1}.")


def clean(c: dict) -> dict:
    """Clamp everything to the allowed range, so a bad answer can never push the rack outside it."""
    c["levels"] = {v: min(1.0, max(0.0, float(c["levels"].get(v, 0.0)))) for v in VOICES}
    c["levels"].update(FIXED)                                         # fixed voices ignore whatever the model wrote
    c["minutes"] = min(4.0, max(1.0, float(c["minutes"])))
    c["ramp_seconds"] = min(90.0, max(10.0, float(c["ramp_seconds"])))
    c["ramp_seconds"] = min(c["ramp_seconds"], c["minutes"] * 30)   # at most half the chapter
    if max(c["levels"].values()) < 0.05:
        c["levels"][VOICES[-1]] = 0.3                                 # never silence every voice (the drone, by default)
    return c


def ask_ollama(history, elapsed, model) -> dict:
    body = {"model": model, "stream": False, "format": schema(), "options": {"temperature": 0.9},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt(history, elapsed)}]}
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(json.loads(resp.read())["message"]["content"])
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach Ollama at {OLLAMA_URL}: {e}")


def _no_ranges(node):
    """The API's JSON schema does not accept minimum/maximum on numbers; clean() clamps the values anyway."""
    if isinstance(node, dict):
        return {k: _no_ranges(v) for k, v in node.items() if k not in ("minimum", "maximum")}
    return node


def ask_claude(history, elapsed, model) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=600, system=SYSTEM,
        messages=[{"role": "user", "content": user_prompt(history, elapsed)}],
        output_config={"format": {"type": "json_schema", "schema": _no_ranges(schema())}})
    return json.loads(next(b.text for b in msg.content if b.type == "text"))


def ask_mock(history, elapsed, model) -> dict:
    rng = random.Random(len(history))
    return {"name": "random", "levels": {v: round(rng.random(), 2) for v in VOICES},
            "minutes": round(rng.uniform(1, 4), 1), "ramp_seconds": rng.uniform(10, 60), "why": "mock"}


FIXED: dict[str, float] = {}   # voices the model does not control (set by --fixed), always at this level
MAX_LEVEL_VOLTS = 5.0   # +5 V is unity on a Morph 4 level input; never send more


def start_es8(first_out: int):
    from es8 import ES8
    cvs = tuple(range(first_out, first_out + len(VOICES)))
    trigger = 8 if 8 not in cvs else 7          # ES8 wants a trigger output; it just stays at 0 V
    return ES8(in_channels=[1], out_channel=trigger, level=0.0, cv_channels=cvs, cv_slew=1.0)


def apply_chapter(es8, first_out: int, c: dict, time_scale: float):
    es8.set_slew(MAX_LEVEL_VOLTS / max(1.0, c["ramp_seconds"] * time_scale))
    for i, v in enumerate(VOICES):
        es8.set_cv(first_out + i, c["levels"][v] * MAX_LEVEL_VOLTS)


def write_grid(history: list[dict], directory: Path) -> Path:
    """Write the chapters as a CV Matrix grid (the Swift app's Load grid format): one row per chapter, levels x 5 V on
    the first outputs in VOICES order, the rest at 0 V. Named "YYYY-MM-DD-HHMM-<first chapter>.json"; never overwrites an existing file."""
    rows = [{"name": c["name"][:40], "time": round(c["minutes"] * 60, 1), "glide": round(c["ramp_seconds"], 1),
             "values": [round(c["levels"][v] * MAX_LEVEL_VOLTS, 2) for v in VOICES] + [0.0] * (8 - len(VOICES))} for c in history]
    slug = "".join(ch if ch.isalnum() else "-" for ch in history[0]["name"].lower()).strip("-") or "piece"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M")
    path, n = directory / f"{stamp}-{slug}.json", 2
    while path.exists():                                              # two runs in the same minute
        path, n = directory / f"{stamp}-{slug}-{n}.json", n + 1
    path.write_text(json.dumps({"rows": rows, "speed": 1.0, "loop": False}, indent=1))
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", choices=["ollama", "claude", "mock"], default="ollama")
    p.add_argument("--model", help="override the model name")
    p.add_argument("--chapters", type=int, default=10, help="how many chapters to write (default 10)")
    p.add_argument("--examples", action="store_true", help="add a worked example of good chapters to the prompt (for small models)")
    p.add_argument("--fixed", default="", help="voices the model does not control, as name=level, e.g. env2=1,drone=1")
    p.add_argument("--voices", type=int, default=4, metavar="N",
                   help="how many voices (1-8, default 4: the Morph 4 voices). Other counts name them voice1..voiceN")
    p.add_argument("--brief", default="", help="the performer's own description of the voices and what they want; "
                   "replaces the built-in description of the four voices")
    p.add_argument("--direction", default="", help="free-text direction from the performer, added to the prompt for every chapter, "
                   "e.g. 'sparse and dark, with long silences'")
    p.add_argument("--live", action="store_true", help="send the levels to the ES-8 and wait out each chapter")
    p.add_argument("--first-out", type=int, default=1, help="first of the four ES-8 outputs (default 1: outputs 1-4)")
    p.add_argument("--write-grid-dir", type=Path, metavar="DIR",
                   help="when done, write the chapters as a CV Matrix grid file in DIR and print its path as GRID_FILE: ...")
    p.add_argument("--time-scale", type=float, default=1.0, help="multiply chapter and ramp times (0.25 = four times faster)")
    args = p.parse_args()
    global SYSTEM
    if not 1 <= args.voices <= 8:
        raise SystemExit("--voices must be between 1 and 8")
    brief = " ".join(args.brief.split())[:1500]
    if brief or args.voices != len(VOICES):
        if args.examples:
            raise SystemExit("--examples describes the default four voices; use it without --brief or --voices")
        VOICES[:] = [f"voice{i + 1}" for i in range(args.voices)]
        SYSTEM = custom_system(brief)
    for item in filter(None, args.fixed.split(",")):
        name, _, level = item.partition("=")
        if name not in VOICES:
            raise SystemExit(f"--fixed: unknown voice {name!r} (voices: {', '.join(VOICES)})")
        FIXED[name] = min(1.0, max(0.0, float(level or 1.0)))
    if FIXED:
        SYSTEM += ("\n\nThe performer holds these voices at fixed levels, so do not try to change them: "
                   + ", ".join(f"{k} = {v:.2f}" for k, v in FIXED.items()) + ". Write the other voices so they work with them.")
    direction = " ".join(args.direction.split())[:500]
    if direction:
        SYSTEM += ("\n\nThe performer's direction for this whole piece (follow it as far as the rules above allow): "
                   + direction)
    if args.examples:
        SYSTEM += example_text()
    model = args.model or (DEFAULT_OLLAMA_MODEL if args.backend == "ollama" else DEFAULT_CLAUDE_MODEL)
    ask = {"ollama": ask_ollama, "claude": ask_claude, "mock": ask_mock}[args.backend]

    history: list[dict] = []
    elapsed = 0.0
    es8 = start_es8(args.first_out) if args.live else None
    chapter_end = time.time()
    print(f"{'LIVE' if es8 else 'Dry run'}: {args.chapters} chapters, backend {args.backend}"
          + ("" if args.backend == "mock" else f" ({model})")
          + (f". Sending 0-5 V on ES-8 outputs {args.first_out}-{args.first_out + 3}; Ctrl-C ends it at 0 V.\n"
             if es8 else ". Nothing is sent to the rack.\n"))
    try:
        run(args, ask, model, history, es8, elapsed, chapter_end)
        if args.write_grid_dir and history:
            print(f"GRID_FILE: {write_grid(history, args.write_grid_dir)}", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if es8:
            es8.close()


def run(args, ask, model, history, es8, elapsed, chapter_end):
    for i in range(args.chapters):
        t0 = time.time()
        c = None
        for attempt in range(3):                      # a model now and then returns cut-off JSON: ask again
            try:
                c = clean(ask(history, elapsed, model))
                break
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                print(f"chapter {i + 1}: bad answer ({e}); " + ("asking again" if attempt < 2 else "giving up"), flush=True)
        if c is None:
            break
        took = time.time() - t0
        if es8:                                   # the model wrote it while the last chapter played; now wait it out
            time.sleep(max(0.0, chapter_end - time.time()))
            apply_chapter(es8, args.first_out, c, args.time_scale)
            chapter_end = time.time() + c["minutes"] * 60 * args.time_scale
        history.append(c)
        elapsed += c["minutes"]
        with LOG.open("a") as f:
            f.write(json.dumps({"backend": args.backend, "model": model, "n": i + 1, "t_min": round(elapsed, 1),
                                "seconds_to_write": round(took, 1), **c}) + "\n")
        lv = "  ".join(f"{v} {c['levels'][v]:.2f}" for v in VOICES)
        print(f"{i + 1:2d}. {c['name'][:22]:22s} {lv}  | {c['minutes']:.1f} min, ramp {c['ramp_seconds']:.0f}s  [{took:.1f}s to write]")
        print(f"      {c['why']}")
    if es8 and history:
        time.sleep(max(0.0, chapter_end - time.time()))   # let the last chapter finish
    if history:
        print(f"\n{len(history)} chapters, {elapsed:.0f} minutes of music. Log: {LOG.name}")
        for v in VOICES:
            xs = [c["levels"][v] for c in history]
            print(f"  {v:6s} min {min(xs):.2f}  max {max(xs):.2f}  mean {sum(xs) / len(xs):.2f}")


if __name__ == "__main__":
    main()
