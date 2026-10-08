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
- Dice clocking (answered 2026-10-07, works by ear): voice 1 is on Dice, voice 2 on Bloom, each with its own
  Contour 1 envelope and its own A-166 + Compare 2 trigger halter. Dice gets the multed A-166 output (the accepted
  trigger, not Bloom's raw clock) in BOTH clock jacks: the lower right one is the X (pitch) clock, which steps X1-X3
  on every pulse; with only the left t clock patched, X1/X3 follow t1/t3 and may rarely step. Bloom can also be
  clocked externally (Rate knob fully left, Clock input, threshold +0.4 V; its Clock Output follows its clock rate).
- Envelope rates: two Walk 4 voltages go through a 4ms SISM (SCALE 0.70, SHIFT -2.4 V, both channels) and then
  Min-Max clamps (floor -4.0 V, ceiling -0.5 V from Source) to each Contour 1 Rise CV. Run `monitor_volts.py` to
  watch it: in 12 minutes nothing left the range. SHIFT reads about 0.2 V light on the real unit.
- Status 2026-10-07: the rack patch above sounds good to the user, and the clamp kept both rate voltages in range over
  a 52-minute run on the 4ms hardware, with two oddities (below). The agent code was not touched.
- Open items: (1) brief spikes of the rate voltages to about +0.02 V (limit -0.5 V) and out1 sitting near -4.18 V
  (limit -4.0 V); the user did not touch knobs, cause unknown, a 200-readings-a-second logger could show them.
  (2) Envelopes felt a little long (rates sit near -2.4 to -2.8 V, 5x to 7x the slider time); options are the Contour
  sliders, SHIFT up 1 V (halves lengths), or the floor knob to -3.0 V (Source value 35). Each volt doubles the time.
  (3) The ES-8's 4 inputs are full (walks, rates); an ES-6 MK3 adds 6 inputs, about $160-199, DC coupling must be on.
  (4) New iMac (rumored M6, October) and a Mac mini model host are on hold; the user decided to do nothing for now.
- Idea to discuss next (user, 2026-10-08): add the Joranalogue Morph 4 so longer envelopes are a bit louder and shorter ones
  a bit quieter, driven by the clamped rate voltage (more negative = longer). Feasible in principle: Morph 4 is four
  VCAs under one master morph CV; the SISM could invert/scale the rate voltage into that CV. Manual: Gear Manuals/Joranalogue.
- Eventual to-do (user, 2026-10-08): test a hosted-LLM version: replace Dice (voice 1) and Bloom (voice 2) as the note
  sources with the Claude API backend, used for NOTES ONLY (existing path: composer.py -> player.py -> MIDI -> Intellijel 1U);
  the rack keeps envelopes, rates and the fifth shift. Spend $20-50 first (set an account spend limit), then decide on a
  Mac mini model host. Not started; the agent stays untouched until the user asks.
- Fifth-shift tuning (2026-10-08): Walk 4 accumulator -> Compare 2 -> A-150-1 gives the 0.58 V fifth; Compare high = fifth ON.
  Goal: home (0 V) dominant, roughly 50/50 with 1-2 minute stays. Results, 15 min each: size 9 o'clock = 1% on; 10:30 = 11% on
  (home stays ~65 s, fifth only 4 s blips); 11 = 26% on but flips every ~14 s. With a Befaco slew limiter (MetaModule In 5/Out 5,
  rise and fall at middle) in front of Compare 2 at size 11, the fifth stayed ON 15+ minutes with no flips. NEXT: size back to
  about 10 o'clock with the slew in, test 15 min. Measure with `level_changes.py` (ES-8 input 1 = A-150 output).
  `walk4+slews cheat sheet.pdf` in Gear Manuals still says size 9; update it once the size settles.
- Ordered: ES-6 MK3 input expander + 15 cm optical cable. Before installing read its manual page: remove the DC-blocking
  jumpers on header GT4 (factory default blocks DC), 10-way ribbon to a header on the ES-8 PCB, 10-pin power.
- Do not run the agent against the rack unprompted. Use `--dry-run` (prints, sends nothing) or `--mock`.

## The user's rig (verify before building on any of this)
- Mac -> iConnectAUDIO2+ (port "DIN") -> MIDI bus. MicroFreak on MIDI channel 7; Intellijel 1U
  MIDI-to-CV on channels 8 and 9 (pitch CV + gate), then 321, Scales quantizers, AJH Synths.
- Envelopes: Joranalogue Contour 1 (rate CV: negative volts = longer envelopes), triggers from a
  Doepfer A-162 with an A-166 + Compare 2 lockout; an A-151 steps the Scales' fifth shift (full 10 V
  trigger double-steps it, about 5 V is right). Two Walk 4 random walks. Squarp Hapax, Mordax scope.
- Contour 1 time settings are its sliders, sitting in the top half of the area labelled slew/loop (set by
  hand, not measured). The rate CVs from the 4ms patch multiply those slider times.
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
- `envelope_log.py` read-only: logs each envelope (ES-8 inputs 1, 2) with rise/fall times and its rate voltage (inputs 3, 4).
- `level_changes.py` read-only: logs each change of a slow two-level voltage on an ES-8 input (e.g. the A-150 fifth shift) and how long each level lasted.
