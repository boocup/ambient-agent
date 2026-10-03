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
in real time. With `--loop`, the next phrase is composed while the current one
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
python ambient_agent.py --ollama --port DIN --loop

# One phrase from Claude, printed instead of played
python ambient_agent.py --dry-run

# Play live, forever, each phrase developing from the last
python ambient_agent.py --port DIN --loop

# Same, to a synth listening on channel 7
python ambient_agent.py --port DIN --channel 7 --loop

# Change the mood and key, and keep a recording
python ambient_agent.py --port DIN --loop --key "A aeolian" --style "misty, sparse, distant bells" --save session.mid
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
| `--channel N` | 8 | MIDI channel, 1-16 |
| `--key "ROOT MODE"` | `D dorian` | Modes: ionian/major, dorian, phrygian, lydian, mixolydian, aeolian/minor, locrian, major-pentatonic, minor-pentatonic |
| `--low`, `--high` | `C2`, `C5` | Note range (C4 = middle C = MIDI 60) |
| `--bpm` | 60 | Tempo |
| `--beats` | 32 | Phrase length in beats |
| `--style "TEXT"` | | Mood hints passed to Claude |
| `--loop` | off | Keep composing and playing |
| `--phrases N` | 0 (forever) | With `--loop`, stop after N phrases |
| `--dry-run` | off | Print gate on/off events instead of sending MIDI |
| `--ollama [MODEL]` | off | Compose with a local model via Ollama (default `qwen2.5:7b`) |
| `--mock` | off | Offline composer, no API calls |
| `--seed N` | | Random seed for `--mock` |
| `--save FILE.mid` | | Write everything played to a MIDI file |
| `--model` | `claude-sonnet-5-5` | Claude model ID |

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

## Notes

- Each phrase is one API call with `claude-sonnet-5-5` at medium effort. At
  the defaults (32 beats at 60 BPM) that's about two calls a minute in `--loop`.
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
python ambient_agent.py --ollama --port DIN --loop
```

Any Ollama model works: `--ollama llama3.1:8b`. On an M1 with 16 GB a
7B model takes about 20 seconds per phrase, which stays ahead of playback at
the default 32 beats / 60 BPM. The phrases are simpler than Claude's but
still develop from one to the next.

## License

MIT. See [LICENSE](LICENSE).
