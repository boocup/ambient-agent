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
- Hosted-LLM notes test DONE (2026-10-09/10) and shelved: `cv_pitch.py` (`--cv-pitch`) plays the melody as 1 V/oct from ES-8 out
  1/2, one note per Contour envelope; it works (dry-run and real) but the user heard it as worse than Dice/Bloom and went back to
  Dice/Bloom. An LLM conductor (mood -> Dice CV knobs) was described and declined; the Mac mini / small local model are TABLED.
  The user wants generative music from the rack; my role is guidance, measuring and notes, not the signal path. Code is kept.
- API billing (2026-10-09): Console credits are prepaid ($4.32 left), auto-reload OFF (keep it off), monthly spend limit
  set to $100 (was the $200,000 default); add an email notification at about $20 if not done. Claude Code logs in with the
  Pro account (/status), so coding sessions do not use the credits. The only API spend so far, about $0.68, was the agent on
  Oct 3-5 (Sonnet 5.5, 174K tokens in, 33K out per the Console Usage page).
- Rack check (2026-10-09, 10 min each, Compare 2 size 11:00, shift 12:30): clamped rates stayed in range (0.2-0.4% of
  seconds out, peaks near 0 V). Envelopes via `envelope_log.py` (Contour outs on ES-8 in 1, 2): voice 1 94 envelopes, rise
  median 2.8 s (1.2-14); voice 2 68 envelopes, rise median 3.5 s (2.4-12); falls about 0.9 s; rise follows the rate
  voltage (corr -0.72, -0.90); rates sit around -1.4 to -1.6 V. The Walk 4 wanders, so any window is only a sample.
  ES-8 in 1, 2 read flat (+0.54, +4.76) before the Contours were patched there. Fifth-shift test not run.
- Fifth-shift tuning (2026-10-08): Walk 4 accumulator -> Compare 2 -> A-150-1 gives the 0.58 V fifth; Compare high = fifth ON.
  Goal: home (0 V) dominant, roughly 50/50 with 1-2 minute stays. Results, 15 min each: size 9 o'clock = 1% on; 10:30 = 11% on
  (home stays ~65 s, fifth only 4 s blips); 11 = 26% on but flips every ~14 s. With a Befaco slew limiter (MetaModule In 5/Out 5,
  rise and fall at middle) in front of Compare 2 at size 11, the fifth stayed ON 15+ minutes with no flips. NEXT: size back to
  about 10 o'clock with the slew in, test 15 min. Measure with `level_changes.py` (ES-8 input 1 = A-150 output).
  `walk4+slews cheat sheet.pdf` in Gear Manuals still says size 9; update it once the size settles.
- Fifth-shift status (2026-10-10): the "always shifting" fault was the slew patched wrong (Compare 2 saw 0 V = window centre). With
  it right, the slewed walk into Compare 2 sits about 1-4 V (median 2.4) and crosses its median every ~8 s. Compare 2 per manual:
  shift knob -5..+5 V (about 1 V per hour on the dial), size = total window width 0..10 V (noon = 5 V), CVs ADD to the knobs.
  Knobs fully CCW + MetaModule Sources (Out 7 shift CV +5.65 V = Source 78.25, Out 8 size CV +3.3 V = Source 66.5) put the upper
  edge at about 2.25 V: fifth ON 34-52% per 10 min (varies with the walk), stays only 7-13 s. Longer stays: a hysteresis patch
  (Compare OUT -> MetaModule -> Min-Max Max with a +1.7 V Source -> size CV) simulated to about 90 s stays but the cabling got
  confusing and was removed; simulated alternative: more Befaco slew (extra smoothing 40-80 s gives median stays 41-75 s).
  The user reset the patch and wants it SIMPLE; start over next time. Measure with ES-8 in 3 = A-150 out (`level_changes.py
  --input 3`) and in 4 = walk into Compare 2 (a read-only recording). The cheat-sheet PDF is NOT updated yet (still says size 9).
  The ES-8 once dropped off USB mid-test (check `find_es8` / the device list first).
- Fifth-shift PLAN for next session (user chose 2026-10-10, "flip each crossing"; keep it simple, do nothing until asked):
  Walk 4 -> A-148 sample and hold (clocked slowly, about 45-60 s; optional) -> Contour 1 GATE input (manual: Schmitt, low below
  2 V / high above 3 V, so 1 V hysteresis built in; output 0/+10 V slewed) -> A-162 (manual: starts on the RISING edge only, so the
  Contour falling gives no trigger; set Len about 0.1 s) -> A-151 set to 2 steps (toggles on every trigger: I/O 1 empty = 0 V,
  I/O 2 = 0.583 V from a Source/attenuator) -> Scales fifth shift. Result: the shift flips on each upward crossing, about 50/50,
  stays roughly a minute (simulated on the recorded walk: 21 changes in 12 min with Schmitt alone). It does NOT follow the walk's
  level. The walk needs to sit around 2.5 V (it does, median 2.4). Open: the two Contour 1s are the voice envelopes, so this needs
  a THIRD Contour 1 (or another way to get the Schmitt). A-150 is no longer needed (A-150 = level-controlled 2-way switch, CV
  threshold about 3.6 V); the user may pull it to free space for the A-148 / ES-6. Doepfer manuals are in Gear Manuals/doepfer
  (I missed that folder once: look in lowercase folder names too).
- Ordered: ES-6 MK3 input expander + 15 cm optical cable. Before installing read its manual page: remove the DC-blocking
  jumpers on header GT4 (factory default blocks DC), 10-way ribbon to a header on the ES-8 PCB, 10-pin power.
- CV pitch mode (2026-10-09, user asked for it; no MIDI): Dice, Bloom, 321 and Scales are out of the loop. ES-8 out 1/2 send
  1 V/oct pitch straight to the two AJH oscillators, quantized in software to Bb minor pentatonic (0 V = the root, notes -1 V to
  +1.9 V). Contour envelopes come back on ES-8 in 1/2 (the A-166/Compare 2 halters still fire them, a clock feeds the A-166); the
  agent steps a voice to its next note only after that voice's envelope falls. Code: `cv_pitch.py`, flag `--cv-pitch`. Tested
  with `--dry-run` (mock and real Sonnet 5.5), NOT yet sent as real voltage. ES-8 in 3/4 (rate taps) are free to reuse.
  The ES-6 should add inputs 5+ later today.
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
  Loose PDFs: COSMOS, HAPAX, Microcosm, Morphader, Marbles. Folders by maker: AJH Synth, ALM, doepfer (A-148, A-150, A-151, A-162, A-166 manuals), FrapTools,
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
