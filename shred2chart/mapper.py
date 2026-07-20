"""IR notes -> Clone Hero 5-lane note events (Stage 4, M4 contour version).

Lane assignment uses a sliding-window pitch contour tracker that replaces
the M3 `pitch % 5` naive mapping:

- A rolling pitch window of the last few notes tracks the relative melodic
  range.  Notes are mapped within 5 lanes proportionally to their position
  inside that window.
- The window is reset (and re-centred) at phrase boundaries: either a
  section-marker tick that was stamped on the note via the `section_tick`
  field, or a rest of at least one quarter note (IR_TICKS_PER_QUARTER).
- Repeated identical pitches stay on the same lane (no jitter rule).
- The open-string chug rule is applied before contour: fret 0 on the
  lowest-tuned string always maps to open note (N 7), regardless of the
  contour window.

Additionally retained from M3:
- Ties merge into sustains (the EOF-confirmed behavior).
- Chord voicing by harmonic interval with anti-repeat nudge.
- Technique flags: hammer_on/pull_off -> forced flip (N 5),
  tap -> tap modifier (N 6, overrides HOPO per spec).
- Sustain threshold: sub-eighth notes get zero sustain; sustains trimmed
  by a 1/32-note gap before the next note on that lane.

Tick conversion: IR is 960 ticks/quarter (PyGuitarPro convention),
.chart is emitted at Resolution=192, so every position/length divides
by 5 (all common note values stay exact integers).

Note-type semantics (N 0-4 lanes, 5 forced, 6 tap, 7 open) are pinned
from the community chart-format docs (TheNathannator's
GuitarGame_ChartFormats), not from memory, per the game plan's mandate.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

IR_TICKS_PER_QUARTER = 960
CHART_RESOLUTION = 192
_DIVISOR = IR_TICKS_PER_QUARTER // CHART_RESOLUTION  # 5

# Sustain rules, in chart ticks (192/quarter): notes shorter than an
# eighth get no sustain (CH convention); sustains are trimmed to leave
# a 1/32-note gap before the next note.
MIN_SUSTAIN = CHART_RESOLUTION // 2  # eighth note = 96
SUSTAIN_GAP = CHART_RESOLUTION // 8  # 1/32 note = 24

OPEN_NOTE = 7
FORCED_FLAG = 5
TAP_FLAG = 6

# Contour window: track the last N distinct pitches to set the range.
_CONTOUR_WINDOW = 12
# Minimum window span in semitones before we start spreading lanes.
_MIN_WINDOW_SPAN = 4
# Rest threshold (in IR ticks) that resets the contour window.
# One bar in 4/4 = 4 quarter notes. Rests shorter than a full bar keep
# the current phrase context; longer gaps signal a new phrase.
_REST_RESET_TICKS = IR_TICKS_PER_QUARTER * 4  # 1 bar (3840 ticks in 4/4)


@dataclass
class ChartNote:
    tick: int  # chart ticks (192/quarter)
    lanes: list[int]  # 0-4, or [OPEN_NOTE]
    sustain: int = 0  # chart ticks
    forced: bool = False
    tap: bool = False
    source: dict = field(default_factory=dict, repr=False)


def _to_chart_ticks(ir_ticks: int | float) -> int:
    return round(ir_ticks / _DIVISOR)


def _merge_ties(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold tied notes into the duration of the note they extend.

    A tied note continues the previous note at the same string+pitch;
    it must not become a new attack. Matching tolerates small gaps
    (rounding, bar boundaries) up to a 64th note.
    """
    tolerance = IR_TICKS_PER_QUARTER // 16
    merged: list[dict[str, Any]] = []
    last_by_string: dict[Any, dict[str, Any]] = {}
    for note in sorted(notes, key=lambda n: n["tick"]):
        key = (note["string"], note["pitch"])
        prev = last_by_string.get(key)
        if (
            note.get("tied")
            and prev is not None
            and abs((prev["tick"] + prev["duration_ticks"]) - note["tick"]) <= tolerance
        ):
            prev["duration_ticks"] = note["tick"] + note["duration_ticks"] - prev["tick"]
            continue
        copy = dict(note)
        merged.append(copy)
        last_by_string[key] = copy
    merged.sort(key=lambda n: n["tick"])
    return merged


def _lowest_tuning_string(notes: list[dict[str, Any]]) -> int | None:
    """The string whose open pitch (pitch - fret) is lowest — the chug
    string in drop tunings."""
    tunings: dict[int, int] = {}
    for note in notes:
        if note["string"] is None or note["pitch"] is None or note["fret"] is None:
            continue
        open_pitch = note["pitch"] - note["fret"]
        current = tunings.get(note["string"])
        tunings[note["string"]] = min(current, open_pitch) if current is not None else open_pitch
    if not tunings:
        return None
    return min(tunings, key=tunings.get)


def _interval_to_gap(semitones: int) -> int:
    """Map a chord interval (semitones) to a chart lane gap (1-4).

    Tight intervals (m2/M2/m3/M3) stay on close lanes; wider ones
    (P4/P5 — i.e. power chords) and beyond spread across skipped
    lanes. Replaces the old always-adjacent (gap=1) rule.
    """
    semitones = abs(semitones) % 12
    if semitones <= 4:   # m2, M2, m3, M3
        return 1
    if semitones <= 7:   # P4, P5 (power chords land here)
        return 2
    if semitones <= 9:   # m6, M6
        return 3
    return 4              # m7, M7, octave


class _ContourTracker:
    """Sliding-window pitch contour for lane assignment.

    Maintains a rolling window of recent pitches to define a local pitch
    range.  New pitches are mapped linearly onto 5 lanes within that range.
    The window resets at phrase boundaries (explicit reset call) and after
    long rests (detected from note timestamps).
    """

    def __init__(self) -> None:
        self._window: deque[int] = deque(maxlen=_CONTOUR_WINDOW)
        self._pitch_to_lane: dict[int, int] = {}
        self._last_tick: int | None = None

    def reset(self) -> None:
        self._window.clear()
        self._pitch_to_lane.clear()
        self._last_tick = None

    def _update_window(self, pitch: int) -> None:
        self._window.append(pitch)
        self._rebuild_cache()

    def _rebuild_cache(self) -> None:
        if not self._window:
            self._pitch_to_lane.clear()
            return
        lo = min(self._window)
        hi = max(self._window)
        span = hi - lo
        # Use at least _MIN_WINDOW_SPAN semitones so notes near each other
        # still spread across lanes rather than collapsing to one.
        effective_span = max(span, _MIN_WINDOW_SPAN)
        new_cache: dict[int, int] = {}
        for p in set(self._window):
            new_cache[p] = round((p - lo) / effective_span * 4)
        self._pitch_to_lane = new_cache

    def lane(self, pitch: int, ir_tick: int) -> int:
        """Return a lane (0-4) for pitch, updating the window."""
        # Rest detection: long gap since last note resets the window.
        if self._last_tick is not None and ir_tick - self._last_tick >= _REST_RESET_TICKS:
            self.reset()
        self._last_tick = ir_tick

        # If the pitch is already in the window, recompute from the existing
        # window (don't grow it again) to keep repeated notes stable.
        if pitch in self._pitch_to_lane:
            return self._pitch_to_lane[pitch]

        # New pitch: add to window, rebuild.
        self._update_window(pitch)
        return self._pitch_to_lane.get(pitch, pitch % 5)


def _assign_lanes_contour(
    group: list[dict[str, Any]],
    chug_string: int | None,
    contour: _ContourTracker,
    ir_tick: int,
) -> list[int]:
    """Lane assignment for one beat's notes using contour tracking.

    Single notes: defer to the contour tracker.
    Chords: use interval-spread voicing (inherited from M3) with the
    contour tracker setting the root lane.
    Open-string chug detection happens first and bypasses the contour.
    """
    if len(group) == 1:
        note = group[0]
        if note["fret"] == 0 and note["string"] == chug_string:
            return [OPEN_NOTE]
        pitch = note["pitch"] or 0
        return [contour.lane(pitch, ir_tick)]

    pitches = sorted({n["pitch"] or 0 for n in group})
    width = min(len(pitches), 3)
    root = pitches[0]
    root_lane = contour.lane(root, ir_tick)

    offsets = [0]
    for prev_p, curr_p in zip(pitches[: width - 1], pitches[1:width]):
        offsets.append(offsets[-1] + _interval_to_gap(curr_p - prev_p))

    span = offsets[-1]
    if span > 4:
        if width == 2:
            offsets = [0, 4]
        else:
            mid = max(1, min(3, round(offsets[1] * 4 / span)))
            offsets = [0, mid, 4]
        span = 4

    # Anchor the chord at the root_lane, clamping to keep all lanes in 0-4.
    base = max(0, min(4 - span, root_lane))
    lanes = sorted({base + o for o in offsets})

    # Update the contour window with additional chord pitches.
    for p in pitches[1:width]:
        contour.lane(p, ir_tick)

    return lanes


def map_notes(
    ir_notes: list[dict[str, Any]],
    section_ticks: list[int] | None = None,
) -> list[ChartNote]:
    """Map a single track's (or blended) IR note list to chart notes.

    section_ticks: optional sorted list of IR ticks where section markers
    occur.  The contour window is reset at each boundary so lane choices
    start fresh per section.
    """
    notes = _merge_ties(ir_notes)
    chug_string = _lowest_tuning_string(notes)
    section_set: set[int] = set(section_ticks or [])

    contour = _ContourTracker()

    # Group simultaneous notes (chords share a tick; chord_id guards
    # against two tracks' blended notes colliding on one tick).
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for note in notes:
        groups.setdefault((note["tick"], note.get("chord_id")), []).append(note)

    chart_notes: list[ChartNote] = []
    for (ir_tick, _), group in sorted(groups.items(), key=lambda kv: kv[0][0]):
        # Reset contour at explicit section boundaries.
        if ir_tick in section_set:
            contour.reset()

        lanes = _assign_lanes_contour(group, chug_string, contour, ir_tick)
        duration = max(n["duration_ticks"] for n in group)
        chart_notes.append(
            ChartNote(
                tick=_to_chart_ticks(ir_tick),
                lanes=sorted(set(lanes)),
                sustain=_to_chart_ticks(duration),
                forced=any(n.get("hammer_on") or n.get("pull_off") for n in group),
                tap=any(n.get("tap") for n in group),
                source={"ir_tick": ir_tick},
            )
        )

    # Collapse groups that landed on the same chart tick (blend seams,
    # rounding): merge their lanes rather than stacking duplicates.
    by_tick: dict[int, ChartNote] = {}
    for note in chart_notes:
        existing = by_tick.get(note.tick)
        if existing is None:
            by_tick[note.tick] = note
        else:
            existing.lanes = sorted(set(existing.lanes) | set(note.lanes))
            existing.sustain = max(existing.sustain, note.sustain)
            existing.forced = existing.forced or note.forced
            existing.tap = existing.tap or note.tap
    result = sorted(by_tick.values(), key=lambda n: n.tick)

    # Sustain policy: shorter than an eighth -> no sustain; otherwise
    # trim to leave a gap before the next note.
    for i, note in enumerate(result):
        if i + 1 < len(result):
            note.sustain = min(note.sustain, result[i + 1].tick - note.tick - SUSTAIN_GAP)
        if note.sustain < MIN_SUSTAIN:
            note.sustain = 0
    return result
