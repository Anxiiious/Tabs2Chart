"""IR notes -> Clone Hero 5-lane note events. Directional wraparound
contour + distinct-lane guarantee for simultaneous notes, chord voicing
removed.

CORE CHANGE: the old _ContourTracker computed each note's lane from its
absolute position inside a min/max pitch window. That caps out — a long
rising run just pins at lane 4 (orange) and flatlines, which is not how
real charts handle scale runs/solos. Real charts treat lane position as
RELATIVE motion: each step up moves the cursor up a lane; hit the
ceiling (4) and the next upward step wraps back to 0 and keeps climbing
— a moving window sliding up (or down) the neck, not a static 5-lane
cap. This is the "staircase"/"Ladder" pattern seen in every fast scalar
run on a real chart (confirmed as a named community convention via the
Clone Hero Wiki; the underlying anchor+motion+wraparound mechanism is
also independently used in the Tensor Hero chart-generation research
paper, motion range [-4,4], matching ours).

Mechanism:
- `_lane_cursor` is a running integer position, NOT clamped to 0-4.
- Each new distinct pitch moves the cursor by a signed step (bigger
  intervals = bigger steps, direction from sign of the interval).
- The visible lane is `_lane_cursor % 5` — this is what gives the wrap.
- Repeated identical pitch: interval is 0, cursor doesn't move, same lane.
- Phrase boundary (section marker or rest >= 1 bar) resets the cursor
  to 0 (green) — a fresh run always starts climbing from the bottom,
  matching real-guitar fretting-hand ergonomics (anchor stays low,
  higher notes are a temporary reach off that anchor) rather than
  anchoring high for descending phrases.

Distinct-lane guarantee: notes sharing a tick (real chords, blend
seams) each get a preferred lane from the tracker, but only the
lowest-pitched note in the group actually advances the cursor's
persistent state — the rest are placed via nearest-free-lane so
simultaneous notes never collide.

KNOWN UNRESOLVED ISSUE: the branch in _assign_group_lanes handling
extra chord-note lanes beyond the anchor note is a placeholder, not a
considered decision — see handoff notes. Chord logic is explicitly
deprioritized this pass; don't treat this as settled.

Still retained: ties merge into sustains, open-string chug rule
(bypasses the cursor entirely), hammer_on/pull_off -> forced flip,
tap -> tap flag, sustain threshold + gap trim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

IR_TICKS_PER_QUARTER = 960
CHART_RESOLUTION = 192
_DIVISOR = IR_TICKS_PER_QUARTER // CHART_RESOLUTION  # 5

MIN_SUSTAIN = CHART_RESOLUTION // 2  # eighth note = 96
SUSTAIN_GAP = CHART_RESOLUTION // 8  # 1/32 note = 24

OPEN_NOTE = 7
FORCED_FLAG = 5
TAP_FLAG = 6

_MAX_LANE = 4  # lanes are 0-4; OPEN_NOTE(7) lives outside this range
_REST_RESET_TICKS = IR_TICKS_PER_QUARTER * 4  # 1 bar


def _interval_to_step(semitones: int) -> int:
    """Signed interval -> unsigned lane-step magnitude. Small stepwise
    motion (the common case in a scale run) moves one lane per note,
    which is what actually produces the staircase wraparound — bigger
    leaps move further so a real interval jump still reads as a jump,
    not just another staircase step.

    NOTE: this specific bucketing (semitone thresholds -> step size) is
    OUR OWN HEURISTIC, not confirmed against real chart data. See game
    plan Open Questions — a run-detection pre-pass that overrides this
    with a flat step-of-1 for detected monotonic runs has been proposed
    but NOT implemented. Do not treat these thresholds as settled.
    """
    semitones = abs(semitones)
    if semitones == 0:
        return 0
    if semitones <= 4:   # half/whole step, up through a third
        return 1
    if semitones <= 7:   # up to a fifth
        return 2
    if semitones <= 9:   # sixth
        return 3
    return 4              # seventh, octave, or bigger


@dataclass
class ChartNote:
    tick: int
    lanes: list[int]
    sustain: int = 0
    forced: bool = False
    tap: bool = False
    source: dict = field(default_factory=dict, repr=False)


def _to_chart_ticks(ir_ticks: int | float) -> int:
    return round(ir_ticks / _DIVISOR)


def _merge_ties(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


class _ContourTracker:
    """Directional wraparound lane cursor — replaces the old min/max
    window. `_lane_cursor` is unbounded; only the modulo at read time
    folds it into 0-4, which is what produces the staircase wrap."""

    def __init__(self) -> None:
        self._lane_cursor: int = 0
        self._last_pitch: int | None = None
        self._last_tick: int | None = None

    def reset(self) -> None:
        self._lane_cursor = 0
        self._last_pitch = None
        self._last_tick = None

    def raw_lane(self, pitch: int, ir_tick: int) -> int:
        """Advance the cursor for `pitch` and return its lane (0-4)."""
        if self._last_tick is not None and ir_tick - self._last_tick >= _REST_RESET_TICKS:
            self.reset()
        self._last_tick = ir_tick

        if self._last_pitch is None:
            self._last_pitch = pitch
            return self._lane_cursor % 5

        interval = pitch - self._last_pitch
        step = _interval_to_step(interval)
        if interval > 0:
            self._lane_cursor += step
        elif interval < 0:
            self._lane_cursor -= step
        # interval == 0 (repeated pitch): cursor unchanged, same lane.
        self._last_pitch = pitch
        return self._lane_cursor % 5


def _nearest_free_lane(preferred: int, taken: set[int]) -> int:
    """Closest unused physical lane to `preferred`, 0-_MAX_LANE. This is
    NOT circular — simultaneous notes are physical fret positions at one
    instant, not a melodic sequence, so no wraparound here."""
    if preferred not in taken:
        return preferred
    for delta in range(1, _MAX_LANE + 1):
        for candidate in (preferred - delta, preferred + delta):
            if 0 <= candidate <= _MAX_LANE and candidate not in taken:
                return candidate
    return preferred


def _assign_group_lanes(
    group: list[dict[str, Any]],
    chug_string: int | None,
    contour: _ContourTracker,
    ir_tick: int,
) -> dict[int, int]:
    """Distinct lane per note in a same-tick group. Open chugs pulled
    out first. Among the rest, the lowest pitch is the one that
    actually advances the cursor (it's the "melody"/anchor note); any
    others get nearest-free-lane placement.

    UNRESOLVED: the placement logic for extra chord-note lanes (i >= 1
    in the loop below) is a placeholder, not a considered decision —
    see module docstring / handoff notes. Needs review before this is
    trusted against a real chord-bearing file.
    """
    lanes: dict[int, int] = {}
    taken: set[int] = set()

    fretted = []
    for note in group:
        if note["fret"] == 0 and note["string"] == chug_string:
            lanes[id(note)] = OPEN_NOTE
        else:
            fretted.append(note)

    fretted.sort(key=lambda n: n["pitch"] or 0)
    for i, note in enumerate(fretted):
        if i == 0:
            preferred = contour.raw_lane(note["pitch"] or 0, ir_tick)
        else:
            # PLACEHOLDER — extra chord notes anchor off the first
            # assigned lane in the group. Not a considered decision;
            # flagged for review, see docstring above.
            preferred = list(lanes.values())[0] if lanes else 0
        lane = _nearest_free_lane(preferred, taken)
        taken.add(lane)
        lanes[id(note)] = lane

    return lanes


def map_notes(
    ir_notes: list[dict[str, Any]],
    section_ticks: list[int] | None = None,
) -> list[ChartNote]:
    notes = _merge_ties(ir_notes)
    chug_string = _lowest_tuning_string(notes)
    section_set: set[int] = set(section_ticks or [])

    contour = _ContourTracker()

    groups: dict[int, list[dict[str, Any]]] = {}
    for note in notes:
        groups.setdefault(note["tick"], []).append(note)

    chart_notes: list[ChartNote] = []
    for ir_tick, group in sorted(groups.items()):
        if ir_tick in section_set:
            contour.reset()

        lane_by_id = _assign_group_lanes(group, chug_string, contour, ir_tick)
        duration = max(n["duration_ticks"] for n in group)
        lanes = sorted(set(lane_by_id.values()))
        chart_notes.append(
            ChartNote(
                tick=_to_chart_ticks(ir_tick),
                lanes=lanes,
                sustain=_to_chart_ticks(duration),
                forced=any(n.get("hammer_on") or n.get("pull_off") for n in group),
                tap=any(n.get("tap") for n in group),
                source={"ir_tick": ir_tick},
            )
        )

    result = sorted(chart_notes, key=lambda n: n.tick)

    for i, note in enumerate(result):
        if i + 1 < len(result):
            note.sustain = min(note.sustain, result[i + 1].tick - note.tick - SUSTAIN_GAP)
        if note.sustain < MIN_SUSTAIN:
            note.sustain = 0
    return result
