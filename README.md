# ambient-agent

A proof of concept: Claude composes slow ambient phrases, and this script plays
them live as MIDI into a Eurorack system.

```
Mac  ->  iConnectivity iConnect (USB MIDI)  ->  MIDI bus  ->  Intellijel 1U MIDI  ->  pitch CV + gate
```

Each phrase comes back from Claude as structured JSON (pitch, start beat,
duration, velocity). The script cleans it up for a single CV/gate voice and
plays it in real time on MIDI channel 8. With `--loop`, the next phrase is
composed while the current one plays, each developing from the last.

## Setup

Requires Python 3.10+.

```bash
cd ~/vcv-dev/ambient-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...     # add to ~/.zshrc to keep it
```

Set the Intellijel 1U MIDI module to receive on **channel 8** (or pass
`--channel` to match it).

## Usage

```bash
# See your MIDI outputs
python ambient_agent.py --list-ports

# Try it without hardware or an API key
python ambient_agent.py --mock --dry-run

# One phrase from Claude, printed instead of played
python ambient_agent.py --dry-run

# Play live, forever, each phrase developing from the last
python ambient_agent.py --port DIN --loop

# Change the mood and key, and keep a recording
python ambient_agent.py --port DIN --loop --key "A aeolian" --style "misty, sparse, distant bells" --save session.mid
```

`--port` takes the full output name or any unique part of it. With the
iConnectAUDIO2+ there are two outputs, `iConnectAUDIO2+ DIN` and
`iConnectAUDIO2+ USB2`, so `--port iConnect` is ambiguous. Use the one that
feeds your MIDI bus (usually `DIN`).

Press **Ctrl+C** to stop. The script sends a note-off for the sounding note
and an All Notes Off (CC 123), so no gate stays high.

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
