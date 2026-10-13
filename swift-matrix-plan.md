# Plan: native Mac version of the CV matrix (Swift / SwiftUI)

Written 2026-10-13 for a fresh session in a NEW folder (suggested: ~/vcv-dev/cv-matrix). Copy this file there. Nothing here is built yet.

## What it is
The Python prototype (`matrix_app.py` + `matrix_app.html`, in the ambient-agent repo) works on the real ES-8: chapters down the left,
eight knobs across (ES-8 outs 1-8, -5 to +5 V), a time and a glide per row, play top to bottom, optional loop, a speed multiplier,
live voltage readout, jump to a row while playing, stop glides everything to 0 V. The user likes the interface and wants it as a
double-click Mac app. Xcode is installed (version not checked: check it and the command-line tools first).

## Rules from the user
- No LLM or network calls in the app. It runs fully offline.
- Short replies, one change at a time, plain opinions, no flattery. Let them listen/test between steps.
- Never delete files without showing exactly what and getting a yes.
- The first real run on the rack is theirs to start; do not drive the ES-8 unprompted.
- Commit after each finished step (tell them first if the repo is new).

## Steps (build and commit one at a time; each leaves a working state)
1. Grid screen only, no audio: SwiftUI rows (name, time, glide, 8 custom knobs: drag up/down, shift for fine, double-click = 0 V),
   add/duplicate/delete row, select row, speed, loop. Autosave to JSON.
2. Timer and play logic with a dry-run (prints/animates the progress bar and computed target voltages, sends nothing).
3. Audio output to the ES-8 (the hard part, see below). Test with ONE row and a multimeter/ES-8 loopback before anything else.
4. Live voltage readout, jump-to-row, live knob edits while playing, Stop glides to 0 V.
5. Load a piece / export a piece (formats below). Polish, app icon, menu bar optional.

## The audio part (port of es8.py: read that file first)
- ES-8 shows up in macOS as one device (the Python code finds it by name "ES-8"; macOS reports 12 in / 16 out but only 4 in / 8 out are jacks).
  Use output channels 1-8 only. Open an output stream on that device; input is optional (the Python version records 1 input but does not need it).
- Full scale: +-1.0 in the stream is about +-10 V on the jack (assumed; measured about -1% at 1/2.5/5 V). So volts/10 = sample value.
- Cap every output at +-5 V in code (the Python cap), whatever the grid says.
- DC-coupled outputs: write a steady sample value per channel. Never jump: glide from the current value to the new target. Python moves
  at one shared slew (V/s) computed so the largest change takes the row's glide time; smaller changes arrive early. Per-channel slew
  would be an easy improvement.
- Audio callback must be real-time safe (no locks that block, no allocation). Keep targets in atomics or a lock-free structure.
- Closing the stream: glide all outputs to 0 V first (the Python close does ~10 V/s), then stop.
- After a rack power cycle the ES-8 can vanish from the Mac until its USB cable is replugged; the stream may fail to open right after (retry,
  show a clear message). Do not treat that as an app bug.
- Test against the real unit step by step. A wrong voltage goes into a real rack (Morph 4 level inputs, Contour rate CVs).

## File formats (keep compatible with the Python tools)
- Grid file `matrix_grid.json`: `{"rows":[{"name":str,"time":seconds,"glide":seconds<=time,"values":[8 volts]}], "speed":float, "loop":bool}`.
- Pieces (`pieces/*.json`, read by `play_piece.py`): `{"title","saved","source","voices":["env1","env2","piano","drone"],"chapters":[{"n","name","minutes","ramp_seconds","levels":{"env1":0-1,...},"out5_8":[4 volts, optional]}]}`.
  Levels 0-1 map to 0-5 V on outputs 1-4 (Morph 4 level CV inputs A-D). Outputs 5-8 are in `out5_8` (ignored by `play_piece.py`).
  Loading a piece fills outs 1-4 with level x 5 V and keeps out5_8 if present.

## Open choices for the user
- Single window only, or a menu bar app too.
- Separate repo (suggested) or a subfolder of ambient-agent.
- Per-channel slew now or later.
- App name and icon.

## Risks
- New audio code is the main risk: build it last, test slowly, keep the +-5 V cap.
- Two codebases: `play_piece.py` and the AI scripts stay in Python; any change to the piece format must be made in both.
- Distribution to others would need an Apple developer account and notarisation; for the user's own Mac it does not.
