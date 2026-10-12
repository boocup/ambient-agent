# ambient-agent: notes for Claude

A hobby project: Python that makes slow ambient music for a Eurorack system. A composer (Claude, a
local model, or an offline random walk) writes phrases, and the program plays them as monophonic MIDI.
It can also read the rack through an Expert Sleepers ES-8 and steer it back. See README.md for every
option; this file is only what a fresh session needs.

## Where things stand
- START HERE (updated 2026-10-12): the rack runs Dice/Bloom for notes (the LLM-notes test lost by ear, see below). Open threads, in the order the user cares:
  (1) the Select 2 fifth-shift test ("SIMPLE TEST PATCH" in the fifth-shift notes below), the user will say when; (2) DONE 2026-10-12: Ethernet benchmark (see `bench_api.py` in Files; `bench_chat.py --since` can still compare this chat's own calls later); (3) offer to
  update the monitor/logging scripts for the ES-6 inputs (inputs 5-10); (4) the walk4+slews cheat-sheet PDF still says size 9 and needs the final fifth-shift
  settings; (5) DONE 2026-10-12: the ModWiggler thread is updated with the cause; os replied "Thanks for the update", nothing more needed. Keep replies short and do one change at a time: the user dislikes long
  answers and uncertainty, and checks claims (verify hardware facts in the manuals under Gear Manuals, including lowercase folder names like `doepfer`).
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
  (3) DONE 2026-10-11: an ES-6 MK3 added 6 inputs (inputs 5-10), GT4 jumpers removed for DC.
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
  a THIRD Contour 1 (or another way to get the Schmitt). A-150 is not needed for the fifth shift (A-150 = level-controlled 2-way switch, CV
  threshold about 3.6 V); it was pulled, then a Doepfer A-150 was put back in the old A-121-3 slot next to the ES-6 (2026-10-12), where it tested clean (see the ES-6 note). Doepfer manuals are in Gear Manuals/doepfer
  (I missed that folder once: look in lowercase folder names too).
- Fifth-shift IDEA to pick up next (2026-10-11, user: "brilliant"): use the Joranalogue SELECT 2 (the user owns one; manual in Gear
  Manuals/Joranalogue). Per its manual: a gate on the select input switches the left signal input (normalled to a precise +5 V) to the
  right one (normalled to 0 V); gate inputs have Schmitt action (low below 2 V, high above 3 V, so about 1 V of hysteresis); a polariser
  knob at the output goes -1 / 0 (centre) / +1 (set it to about +0.117 so +5 V becomes about 0.583 V, the fifth); a HOLD input freezes the
  output (stores for minutes, so a slow gate on hold could make the shift change only about once a minute). Proposed chain: slewed walk
  into the select gate -> output A -> Scales shift, signal inputs left unpatched. It FOLLOWS the walk level with hysteresis; it does not flip
  on each crossing (the A-162 -> A-151 toggle chain is still the alternative). Needs no A-150, Compare 2, third Contour or attenuator.
  Not tried yet. Cautions from the manual: if the A output is unpatched it mixes into B. Gear list: see memory file gear-list.
  SIMPLE TEST PATCH (agreed 2026-10-12, user will do it later; rack was blank): Walk 4 accumulator (cheat-sheet knobs: rate 11:30, constrain 8:00, trend fully CCW, volatility noon, 0.5 V into the value input; slew optional) -> Select 2 A SELECT gate; A output -> ES-6 jack 1 (ES-6 jacks read accurately; inputs 5-10 in the scripts); a mult of the walk -> ES-6 jack 2. Leave the A signal inputs, hold and UNITY unpatched (unity disables the polariser knob; unpatched left = +5 V, right = 0 V). Knob A slightly clockwise of centre (centre = gain 0, max = +1, so about +0.12 for 0.583 V); tune it by READING ES-6 jack 1, not by dial position. Expected per the manual: walk under 2 V -> left input (+5 V) x gain = about 0.58 V (fifth); walk over 3 V -> right input (0 V) = home; between 2 and 3 V it holds. Then log 10 min (percent on, stay lengths). Nothing goes to the Scales until that works.
- ES-6 mk3 installed and working (2026-10-11). Mapping: ES-8 jacks 1-4 = inputs 1-4; ES-6 jacks 1-6 = inputs 5-10 (use `--es8-expanded`);
  inputs 11-12 have no jacks (ignore). Setup: 10-way ribbon (red stripe down) ES-6 to the ES-8 PCB header, optical cable from the ES-6's
  single digital OUTPUT to the ES-8 ADAT IN (Expert Sleepers' ES-6 page: "Digital output: optical, ADAT Lightpipe"; their forum: you have
  to use the ADAT connectors), and all three GT4 jumpers REMOVED (jumpers fitted = DC blocking, so steady CV decays to 0; verified: with
  them off, 3 V reads 3.01 V and 4 V reads 3.96 V, steady). After any rack power cycle the ES-8 can vanish from the Mac: replug its USB
  cable (it dropped off 3 times on 2026-10-11); a stream may fail to open right after (retry). The ES-6 jacks read accurately; the ES-8's own
  jacks read about 5% low (4 V reads 3.82 V; a Contour's +10 V peak read 9.4 V before the ES-6 existed). Forum summaries say ES-8 inputs are
  uncalibrated (scale and offset in software if exact volts matter); not corrected in my scripts. `monitor_volts.py` and the other logging
  scripts are limited to 4 inputs: NOT yet updated for the ES-6 inputs (offer to do it). RESOLVED (2026-10-12): the ES-6's channels 3-8 (even the two with no jacks) went to junk (swings of about +-10 V, or steps of about 0.667 V) 10-100 s after a stream opened, highest channels first, while jacks 1-2 and the ES-8's own jacks stayed clean. Jumpers (GT4 on or off), the ES-8 clock source (ADAT Clock vs Internal) and just mounting did NOT change it; reseating the ribbon and optical cable gave one clean run with the modules loose. The cause was the ES-6 being PRESSED against its bulky neighbour (a Doepfer A-121-3) in a tight rack: removing that module (the only change) turned a junk run clean, and 3 runs in a row (5, 5, 10 min, 12 inputs, 0 episodes) stayed clean with a gap. A Doepfer A-150 now sits in the old A-121-3 slot beside the ES-6 and tested clean too (10 min patched, 5 min with all cables out); the user cancelled a blank-panel order. Mechanism unknown (pressure on the board or connectors, or coupling). If junk returns, check spacing first. Check with `junk_watch`-style logging (open the stream, log when |v| > 0.3 V). With the jumpers off all six jacks hold steady DC (jacks 1-6 read 1.018, 2.003, 2.968, 3.870, 0.920, 1.954 V for about 1, 2, 3, 4, 1, 2 V) and empty even jacks beside driven odd ones show no noise or meaningful bleed. The ES-8's out 1 -> in 2 loopback is linear at 95.9% (the ES-8 is uncalibrated, as the forum says; no correction wanted). Forum thread (ModWiggler, Expert Sleepers section): os replied that the junk was not expected and the jumpers should not matter; the user posted the cause and os answered "Thanks for the update" (closed 2026-10-12). Expert Sleepers' docs live in Gear Manuals/expertsleepers (HTML).
- ES-6 MK3 is installed (see the ES-6 note above; the 15 cm optical cable goes ES-6 OUT to ES-8 ADAT IN).
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
- `bench_api.py` times calls to hosted Claude (streams one short fixed reply on claude-sonnet-5-5, 10 timed calls plus a no-key network check; costs a fraction of a cent) and appends to `bench_results/api_bench.jsonl`, labelled by the default connection (Wi-Fi/Ethernet). `python bench_api.py --compare` prints medians per connection. Wi-Fi baseline saved 2026-10-12 (first text about 0.60 s, total about 1.28 s, about 120 tokens/s; network only: connect 15 ms, first byte 58 ms). Ethernet run 2026-10-12 (2 runs, 20 calls, Wi-Fi off): network first byte 35 ms (Wi-Fi 58), first text 0.52 s (0.60), total 1.25 s (1.28), steadier; only a small gain, not noticeable in chat.
- `bench_chat.py` (read-only, free) reads the saved Claude Code session transcripts (~/.claude/projects/<project>/*.jsonl) and works out how long this chat's own calls took: first piece, total, and a fit of fixed seconds + seconds per 100 output tokens, by day; `--since "YYYY-MM-DD HH:MM"` prints before vs after (use it around the Ethernet change). Baseline 2026-10-03 to 10-12 (1,407 requests): total median 5.0 s, first piece 3.5 s, fixed about 2.3 s + 0.6 s per 100 output tokens; slower on 10-10 (median 11 s). Output length is the main driver (corr 0.72); conversation size matters little until about 400K tokens (short replies about 2 s below that, about 4 s at 400-600K, small samples). Network only is about 60 ms, so Ethernet should barely change it. It does not include tool run times.
