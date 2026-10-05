"""Which of the agent's notes actually sounded?

The agent knows when it sent each note. A rack with a lockout (e.g. a logic gate that blocks a new trigger while an
envelope is still running) only fires some of them. The trigger that comes out the far side, read on an ES-8 input,
says which. This matches the two lists up: each trigger belongs to the nearest note sent shortly before it.
"""

from __future__ import annotations

import statistics
import threading
from dataclasses import dataclass


@dataclass
class VoiceStats:
    sent: int                    # notes sent (resolved) this window
    fired: int                   # of those, how many produced a trigger
    latency_ms: float | None     # median delay from note to trigger
    extra: int                   # triggers with no note to explain them

    @property
    def blocked(self) -> int:
        return self.sent - self.fired


class TriggerMatcher:
    WINDOW = 0.25   # a trigger must arrive within this long after its note
    SLOP = 0.03     # tolerated clock disagreement (a trigger may seem to come slightly before its note)

    def __init__(self, voices: list[str]):
        self._lock = threading.Lock()
        self._notes: dict[str, list[float]] = {v: [] for v in voices}
        self._triggers: dict[str, list[float]] = {v: [] for v in voices}
        self.ever_seen: dict[str, bool] = {v: False for v in voices}

    def note_sent(self, voice: str, t: float):
        with self._lock:
            if voice in self._notes:
                self._notes[voice].append(t)

    def trigger_seen(self, voice: str, t: float):
        with self._lock:
            if voice in self._triggers:
                self._triggers[voice].append(t)
                self.ever_seen[voice] = True

    def take(self, now: float) -> dict[str, VoiceStats]:
        """Match what has been recorded so far. Notes too recent to be resolved are kept for the next call."""
        cutoff = now - self.WINDOW - self.SLOP
        out = {}
        with self._lock:
            for voice in self._notes:
                notes = sorted(self._notes[voice])
                triggers = sorted(self._triggers[voice])
                matched_notes: set[int] = set()
                matched_triggers: set[int] = set()
                latencies = []
                for j, tr in enumerate(triggers):
                    best = None
                    for i, n in enumerate(notes):
                        if i in matched_notes:
                            continue
                        if -self.SLOP <= tr - n <= self.WINDOW and (best is None or n > notes[best]):
                            best = i
                    if best is not None:
                        matched_notes.add(best)
                        matched_triggers.add(j)
                        latencies.append((tr - notes[best]) * 1000)
                resolved = [i for i, n in enumerate(notes) if i in matched_notes or n <= cutoff]
                out[voice] = VoiceStats(
                    sent=len(resolved),
                    fired=len(matched_notes),
                    latency_ms=statistics.median(latencies) if latencies else None,
                    extra=sum(1 for j, tr in enumerate(triggers) if j not in matched_triggers and tr <= cutoff),
                )
                self._notes[voice] = [n for i, n in enumerate(notes) if i not in set(resolved)]
                self._triggers[voice] = [tr for j, tr in enumerate(triggers)
                                         if j not in matched_triggers and tr > cutoff]
        return out
