# ambient-agent: notes for Claude

A hobby project: Python that makes slow ambient music for a Eurorack system. A composer (Claude, a
local model, or an offline random walk) writes phrases, and the program plays them as monophonic MIDI.
It can also read the rack through an Expert Sleepers ES-8 and steer it back. See README.md for every
option; this file is only what a fresh session needs.

## Where things stand
- Direction (2026-10-06): the user pulled the AI patch from the rack and wants to start fresh WITHOUT
  local or hosted AI (a Marbles clone, "Dice", plus the rack). Hold off on changing or slimming down
  the agent until they ask. Everything built is committed; delete nothing.
- Dice is a clone of Mutable Instruments Marbles; use `marbles_user_manual.pdf` (see manuals below).
- Open question: does Dice have an external clock input, so its pitch advances only on accepted notes?
  Per the Marbles manual: yes. The X section (the three pitch voltages) has its own external clock input,
  and the three X outputs step on each pulse; unpatched, X follows the t section's clock instead. The t
  section has an external clock input too. Not yet checked on the Dice itself. Bloom (QU-Bit) can also be
  clocked externally: Rate knob fully left, Clock input, trigger threshold +0.4 V.
- Do not run the agent against the rack unprompted. Use `--dry-run` (prints, sends nothing) or `--mock`.

## The user's rig (verify before building on any of this)
- Mac -> iConnectAUDIO2+ (port "DIN") -> MIDI bus. MicroFreak on MIDI channel 7; Intellijel 1U
  MIDI-to-CV on channels 8 and 9 (pitch CV + gate), then 321, Scales quantizers, AJH Synths.
- Envelopes: Joranalogue Contour 1 (rate CV: negative volts = longer envelopes), triggers from a
  Doepfer A-162 with an A-166 + Compare 2 lockout; an A-151 steps the Scales' fifth shift (full 10 V
  trigger double-steps it, about 5 V is right). Two Walk 4 random walks. Squarp Hapax, Mordax scope.
- ES-8: 4 input and 8 output jacks (macOS also lists ADAT channels, 12 in / 16 out; the extras are not
  jacks). Input plan: 1 and 2 voice triggers, 3 and 4 walks. Outputs: 1 shift trigger, 3 and 4 rate CVs.
- The Hapax can send a follower as CC 3 on channel 15 over USB (input "HAPAX"); it clips above ~5 V.
- Gear manuals (PDFs, iCloud, may need downloading first): `~/Library/Mobile Documents/com~apple~CloudDocs/Gear Manuals`.
  Loose PDFs: COSMOS, HAPAX, Microcosm, Morphader, Marbles. Folders by maker: AJH Synth, ALM, FrapTools,
  Instruo, Intellijel, Joranalogue, MakeNoise, Moog, OXI, QU-Bit, XAOC, plus a Jupiter-X USB backup.
  Read the right manual before stating a hardware fact. QU-Bit/ holds the Bloom manual. PDFs: no
  pdftotext here; extract text with a small Swift PDFKit script (the Read tool's PDF mode needs poppler).

## Working with this user
- Retired Technology Director; music (Eurorack) is now a hobby. Plain language, honest answers over agreeable ones, push back.
- TEXT ONLY: we just type to each other; no spoken replies.
- Check hardware facts and prices before building (I got the ES-8 jack count and a price wrong).
  Build small steps and let them listen between steps. Say when I am waiting on something.
- Keep command output short; do not print huge listings.
- Never delete files without showing exactly what and getting a yes. The Trash can hold tax and
  legal documents: look before emptying. Moving to the Trash (via Finder) is the reversible way.
- Sessions get slow when long: suggest a fresh session per topic.

## Practical
- Python: use `.venv` (it auto-activates in new terminals opened in the project). `python3` outside it.
  This is zsh: an unquoted variable holding several words is NOT split (use `${=var}`).
- The Anthropic key is only in ~/.zshrc. Never put it in the repo. Tests that must not call the API:
  `env -u ANTHROPIC_API_KEY python ambient_agent.py --mock --dry-run ...`.
- Tests are ad hoc: run the agent with `--mock --dry-run` and read the log. No test suite yet.
- Commit and push each finished change (private GitHub repo, branch main).

## Files
- `ambient_agent.py` CLI, compose/play loop, conductor mode. `composer.py` Claude, Ollama, mock.
- `music.py` scales, note cleanup. `form.py` sections, rhythm, memory, novelty. `player.py` MIDI out.
- `feedback.py` MIDI CC feedback and peak detection. `es8.py` ES-8 reading, triggers, slow voltages.
- `triggers.py` matches notes sent with triggers that fired. `calibrate_rate.py` measures envelope rate.
- `monitor_volts.py` read-only: prints the 4 ES-8 input voltages every second and flags any input outside a range.
