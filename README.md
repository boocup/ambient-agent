# ambient-agent

A proof of concept: Claude composes slow ambient phrases, and this script plays
them live as MIDI on any monophonic synth: a hardware synth, a Eurorack
MIDI-to-CV module, or a soft synth.

It was built and tested with:

```
Mac  ->  iConnectivity iConnectAUDIO2+  ->  MIDI bus  ->  Arturia MicroFreak (channel 7)
                                                      ->  Intellijel 1U MIDI -> pitch CV + gate (channel 8)
```

Each phrase comes back from Claude as structured JSON (pitch, start beat,
duration, velocity). The script cleans it up for a single voice and plays it
in real time. With `--continuous`, the next phrase is composed while the current one
plays, each developing from the last.

## Setup

Requires Python 3.10+.

```bash
cd ~/vcv-dev/ambient-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### API key

You need an Anthropic API key. Create one at
[console.anthropic.com](https://console.anthropic.com/settings/keys).
API usage is billed separately from a Claude Pro/Max subscription, so add a
few dollars of credit under Settings → Billing. $5 lasts a long time here.
Turn off auto-reload if you want that to be a hard cap.

Put the key in your shell, not in this project:

```bash
echo "export ANTHROPIC_API_KEY='sk-ant-...'" >> ~/.zshrc && source ~/.zshrc
```

The script reads it from the environment; it never needs to be in the repo.

### Synth

Set your synth to receive on **channel 8** (the default), or pass `--channel`
to match it, e.g. `--channel 7`.

## Usage

```bash
# See your MIDI outputs
python ambient_agent.py --list-ports

# Try it without hardware or an API key
python ambient_agent.py --mock --dry-run

# Free and offline: compose with a local model via Ollama
python ambient_agent.py --ollama --port DIN --continuous

# One phrase from Claude, printed instead of played
python ambient_agent.py --dry-run

# Play live, forever, each phrase developing from the last
python ambient_agent.py --port DIN --continuous

# Same, to a synth listening on channel 7
python ambient_agent.py --port DIN --channel 7 --continuous

# Two voices composed together: a melody on channel 7, a bass line on 12
python ambient_agent.py --port DIN --continuous --track 7:melody:C3-C5 --track 12:bass:C1-C3

# Change the mood and key, and keep a recording
python ambient_agent.py --port DIN --continuous --key "A aeolian" --style "misty, sparse, distant bells" --save session.mid
```

`--port` takes the full output name or any unique part of it. With the
iConnectAUDIO2+ there are two outputs, `iConnectAUDIO2+ DIN` and
`iConnectAUDIO2+ USB2`, so `--port iConnect` is ambiguous. Use the one that
feeds your MIDI bus (usually `DIN`).

Press **Ctrl+C** to stop. The script sends a note-off for the sounding note
and an All Notes Off (CC 123), so no gate stays high.

To check what's actually being sent, a MIDI monitor such as MIDI View will
show the outgoing notes. Some monitors call middle C "C3", so notes may
appear an octave lower than this script names them; the MIDI numbers match.

## Options

| Flag | Default | |
|---|---|---|
| `--list-ports` | | List MIDI outputs and exit |
| `--port NAME` | | MIDI output (full name or unique part) |
| `--channel N` | 8 | MIDI channel, 1-16 (single track) |
| `--track CH[:NAME[:LOW-HIGH]]` | | Add a voice, e.g. `7:melody` or `12:bass:C1-C3`. Repeat for more; replaces `--channel` |
| `--key "ROOT MODE"` | `D dorian` | Root like `D`, `Bb` or `F#` (flat keys are spelled with flats). Modes: ionian/major, dorian, phrygian, lydian, mixolydian, aeolian/minor, locrian, major-pentatonic, minor-pentatonic |
| `--low`, `--high` | `C2`, `C5` | Note range (C4 = middle C = MIDI 60); the default for every track |
| `--bpm` | 60 | Tempo |
| `--beats` | 32 | Phrase length in beats |
| `--style "TEXT"` | | Mood hints passed to Claude |
| `--continuous` | off | Keep composing and playing |
| `--phrases N` | 0 (forever) | With `--continuous`, stop after N phrases |
| `--dry-run` | off | Print gate on/off events instead of sending MIDI |
| `--ollama [MODEL]` | off | Compose with a local model via Ollama (default `qwen2.5:7b`) |
| `--mock` | off | Offline composer, no API calls |
| `--seed N` | | Random seed for `--mock` and the form planner |
| `--form on\|off` | `on` | Plan sections with register windows and phrase memory (see below); `off` only develops the previous phrase |
| `--memory N` | 8 | With `--form on`: how many recent phrases the model is shown as summaries |
| `--retry-similar X` | 0 (off) | Compose a phrase once more if its similarity to a recent one is above X (0-1). Costs an extra call, so leave tempo room |
| `--save FILE.mid` | | Write everything played to a MIDI file |
| `--model` | `claude-sonnet-5-5` | Claude model ID |
| `--feedback PORT:CH:CC` | | Listen to a CC from the rack, e.g. `DIN:15:3` |
| `--peak N\|auto` | `auto` | With `--feedback`: what counts as a peak. `auto` finds the top of the CC's own range over the last minute; a number (e.g. `91`) means that CC value or higher |
| `--key-change` | off | Also move to `--peak-key` for one phrase after a peak |
| `--peak-key "ROOT MODE"` | up a fifth | With `--key-change`: key for the excursion |
| `--peak-cooldown N` | 3 | With `--key-change`: ignore new peaks for N phrases after a key change |
| `--peak-out CH:CC[:LEVEL]` | `15:20:64` | On each peak, pulse this CC to LEVEL, then 0 after 100 ms, out `--port` for the rack (64 ≈ 5 V in VCV's MIDI CC→CV); `off` to disable |
| `--feedback-debug` | off | Print every feedback value (CC, or ES-8 readings twice a second) |
| `--es8` | off | Use an Expert Sleepers ES-8 directly (no Hapax, no VCV Rack); see "ES-8 direct" below |
| `--es8-follower N` | 1 | With `--es8`: the ES-8 input carrying the envelope follower; peaks are found in it |
| `--es8-walk N[,N...]` | 2 | With `--es8`: more inputs summarized every phrase, e.g. random walks: `2` or `3,4` (0 = none) |
| `--es8-out CH[:LEVEL]` | `1:0.5` | With `--es8`: the output that gets the 100 ms peak trigger, and its level as a fraction of full scale |
| `--peak-gap SECONDS` | 0 | With `--feedback` or `--es8`: at least this long between peak triggers |
| `--rack-state on\|off` | `on` | With `--es8`: tell the model how active the rack was and where the walk sits, as a gentle nudge |

## ES-8 direct (no Hapax, no VCV Rack)

An ES-8 shows up to the Mac as an ordinary audio device with DC-coupled jacks,
so the agent can read CV from its inputs and send triggers out of its outputs
by itself (`es8.py`, using `sounddevice`). With `--es8`:

```bash
python ambient_agent.py --port DIN --continuous --bpm 40 --key "Bb minor-pentatonic" \
  --track 8:melody:C3-C5 --track 9:bass:C1-C3 --es8
```

- **Input 1** (`--es8-follower`) carries an envelope follower of the music. Peaks
  are found in it the same way as in the MIDI version (`--peak auto`, or a number
  on a 0-127 scale), and each peak sends a 100 ms trigger out **output 1**
  (`--es8-out`). With `--peak-gap 90` that is at most one trigger per 90 s.
- **Further inputs** (`--es8-walk 3,4`) are summarized every phrase, each on its
  own: its range, which way it moved, and where it sits in its recent range.
- With `--rack-state on` the model is told, as a gentle nudge, how active the
  rack was and where the walk sits (a very active rack: leave space; a high
  walk: nudge the melody up).
- `--es8` and `--feedback` (the Hapax route) are alternatives.

Voltages assume the ES-8's +-1.0 is about +-10 V; calibrate the trigger level
with a meter or scope. The trigger level that worked for a Doepfer A-151 was
0.5 (about 5 V); a full 10 V made it step twice. The output always returns to
0 V, including when the program stops. The device is found by name, so its
number in the system's audio list doesn't matter. Per-phrase log lines show
`audio glitch(es)` if the Mac was ever too busy to keep the audio steady.

### Slow control voltages and envelope-rate calibration

`ES8` can also hold slow DC voltages on outputs (`set_cv`): they glide to their
target (1 V/s by default, never jumping), are capped at +-5 V, and return to 0 V
when the program stops. The aim is to vary an envelope generator's rate from the
agent. A Contour 1's rate CV spans a very wide time range (about 500 us to 30 s
over 0-10 V per its manual), so measure before choosing a range:

```bash
# patch: ES-8 output 2 -> the envelope's Rate CV in; the envelope's output -> ES-8 input 5
python calibrate_rate.py --out 2 --measure 5 --port DIN --channel 8
```

It sweeps 0-5 V, triggers the envelope with a MIDI note at each step, records
the envelope, and prints and saves (`rate_calibration.json`, not committed) the
rise and fall times. Stop the agent first.

## Keeping long sessions from repeating (form)

Asking the model to "develop the previous phrase" every time pulls each phrase
toward the last one, and over a long session the music settles into a rut. So
the script plans the shape instead (`form.py`), with `--form on` by default:

- **Sections.** A statement introduces a new motif, then develops it for a
  phrase or two; a contrast leaves it behind (new rhythm, new contour, sparse or
  busy); a space is mostly silence; a return brings the motif home, transformed.
  Each phrase's log line shows its role, density and register windows.
- **Register windows.** Each section confines each voice to the low, middle or
  high part of its range. This is applied to the allowed notes themselves, so
  the model cannot ignore it.
- **Memory.** The model is shown a compact summary of the last 8 phrases
  (rhythm onsets, contour, range) and told not to repeat them.
- **No anchoring.** Contrast, space and return phrases are not shown the
  previous phrase, because showing it is what pulls every phrase toward the last.
- **Novelty.** Every phrase prints a 0-1 score of how different it is from the
  last four (1.00 = brand new), and a session average is printed on exit. It is
  also computed with `--form off`, so you can compare the two.

```
Phrase 7 [contrast 1/1 · busy · melody:high bass:low]: ...
  novelty 0.62 vs the last 4 (1.00 = brand new)
```

## Multiple tracks

Each `--track` is one monophonic voice on its own MIDI channel. All voices
are composed together in a single request, so the model can make them fit:
separate registers, one moving while another holds, consonant meetings. The
track name (`melody`, `bass`, `drone`, ...) tells the model the voice's role.

More voices means more notes per request, so composing takes longer. With
the local 7B model on an M1, two tracks take about 37 seconds per 32-beat
phrase; use `--bpm 50` or `--beats 48` so playback stays ahead.

`--save` writes one MIDI track per channel.

## Feedback from the rack

An envelope follower in the rack can steer the agent. Get its CV to the Mac
as a MIDI CC - for example Envelope follower → Mordax (scale to 0-5 V) →
Hapax CV in → mod matrix → CC 3 on channel 15 → Hapax USB to the Mac - then:

```bash
python ambient_agent.py --ollama --port DIN --continuous --bpm 40 --key "Bb minor-pentatonic" \
  --track 8:melody:C3-C5 --track 9:bass:C1-C3 --feedback HAPAX:15:3
```

By default (`--peak auto`) the agent works out for itself what a peak is: it
watches the last minute of the CC and fires when the value climbs near the top
of that range, then re-arms once it falls back toward the middle, so one swell
is one peak. This matters because an envelope that idles high (the Hapax maps
0 V to about CC 64, so a hot follower sits around 118-125) would fire a fixed
threshold on every phrase. It ignores a flat signal, and learns for the first
8 seconds. At most one peak is announced per phrase. A number such as
`--peak 91` restores the fixed threshold. A peak only sends the pulse below,
so the rack decides what happens (e.g. a quantizer's shift input); the agent
stays in its key.

### Optional: key change in the agent

With `--key-change`, a peak also makes a later phrase modulate for one
phrase (default: up a fifth, so D dorian → A dorian) and the next one
returns home. Because each phrase is composed while the previous one plays,
the change lands one phrase after the peak is heard. Don't combine this with
a quantizer that also shifts on the pulse, or the two transpositions stack.
Debug lines show it happening:

```
  [feedback   3.54s] PEAK: CC3 = 95 (>= 91)
  [feedback] last phrase: 12 msgs, min 80, avg 88, max 95
  [key] peak (max 121): phrase 4 will be in A dorian
Phrase 4 [A dorian]: ...
  [key] phrase 5 returns home to D dorian
```

### Peak pulse back to the rack

Each peak also sends a short pulse out `--port`: CC 20 on channel 15 jumps to
64 and returns to 0 after 100 ms. VCV's MIDI CC→CV maps 0-127 to 0-10 V, so
64 gives a ~5 V trigger; a Doepfer A-151 stepped on both edges of a full 10 V
pulse, so set the level with the third number, e.g. `--peak-out 15:20:50`. In the rack, a MIDI-to-CV module mapped to
that CC turns it into a trigger. It fires once per phrase at the moment of
the peak, independent of the key-change cooldown. Change it with
`--peak-out 15:21`, or turn it off with `--peak-out off`.

```
  [feedback  23.40s] PEAK: CC3 = 115 (auto: range 80-123, peak at 114)
  [feedback] -> pulsed CC20 on channel 15
  [feedback] last phrase: 149 msgs, min 80, avg 89, max 121, peaks 1 (auto: range 80-123, peak at 114)
```

## How notes are cleaned up

Claude is asked for monophonic phrases, but the script enforces it anyway
before anything is played (`music.py`, `clean_phrase`):

- Notes outside the phrase are dropped; notes running past the end are cut.
- Pitches are snapped to the nearest note of the scale within the range.
- **Strictly monophonic:** if notes overlap, the earlier one is trimmed to end
  0.05 beats before the next, so the gate drops and the next note retriggers.
  Notes starting at the same time keep only the first.
- Velocity is clamped to 1-127.

## Files

- `ambient_agent.py` - command line, compose/play loop, Ctrl+C cleanup
- `composer.py` - Claude composer (structured JSON output) and the mock composer
- `player.py` - MIDI port selection, real-time playback, panic, `.mid` export
- `music.py` - note names, scales, phrase cleanup
- `feedback.py` - listens for a CC from the rack and summarizes it per phrase
- `es8.py` - reads CV from an ES-8's inputs, sends triggers and slow control voltages out of its outputs
- `calibrate_rate.py` - measures an envelope's rise/fall times against a rate voltage from the ES-8
- `form.py` - sections, register windows, phrase memory and the novelty score

## Notes

- Each phrase is one API call with `claude-sonnet-5-5` at medium effort. At
  the defaults (32 beats at 60 BPM) that's about two calls a minute in `--continuous`.
- If composing ever takes longer than a phrase plays, playback waits for it
  (the script says so).
- Requests opt into the API's server-side refusal fallback. It's very unlikely
  to matter for music, but it means a declined request is retried on another
  model instead of failing.

## Running locally with Ollama

No API key or internet needed. Install [Ollama](https://ollama.com), make sure
it's running, then download a model once (about 4.7 GB):

```bash
ollama pull qwen2.5:7b
python ambient_agent.py --ollama --port DIN --continuous
```

Any Ollama model works: `--ollama llama3.1:8b`. On an M1 with 16 GB a
7B model takes about 20 seconds per phrase, which stays ahead of playback at
the default 32 beats / 60 BPM. The phrases are simpler than Claude's but
still develop from one to the next.

## License

MIT. See [LICENSE](LICENSE).
