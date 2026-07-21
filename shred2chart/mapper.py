"""IR notes -> Clone Hero 5-lane note events. Directional wraparound
contour for single notes, generalized to chords via a scored-candidate
chord-shape heuristic; chord voicing (interval-spread) remains removed.

CORE CHANGE (single notes): the old _ContourTracker computed each note's
lane from its absolute position inside a min/max pitch window. That caps
out — a long rising run just pins at lane 4 (orange) and flatlines, which
is not how real charts handle scale runs/solos. Real charts treat lane
position as RELATIVE motion: each step up moves the cursor up a lane; hit
the ceiling (4) and the next upward step wraps back to 0 and keeps
climbing — a moving window sliding up (or down) the neck, not a static
5-lane cap. This is the "staircase"/"Ladder" pattern seen in every fast
scalar run on a real chart (confirmed as a named community convention via
the Clone Hero Wiki; the underlying anchor+motion+wraparound mechanism is
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

CHORDS: a same-tick group's lowest-pitched note (the "anchor") advances
the shared cursor exactly like a single note above — this is what keeps
the cursor consistent across mixed chord/single-note runs. The rest of
the chord's lanes come from `_chord_shape_candidates` / `_rank_chord_shape`:
every legal way to place the chord's notes on distinct lanes is generated
(there are only C(5,k) of them, at most 10), each is ranked by how well it
continues the established melodic/harmonic motion, avoids re-flattening at
the ceiling/floor, shows harmonic change from the previous chord (or keeps
a genuinely repeated chord's shape stable instead of jittering), nudges
away from a shape used a couple of chords ago, and reads cleanly — and the
top-ranked shape is used. The mapper intentionally chooses among multiple
valid chart representations of a chord; there is no single "correct" lane
assignment for a chord, so do not "fix" this back into a single
deterministic interval-mapping rule. See `_rank_chord_shape` for the exact
criteria; enable DEBUG logging to see every candidate's score breakdown
for a given chord.

Distinct-lane guarantee: every note in a same-tick group always lands on
its own lane (or, for open chugs, the OPEN_NOTE sentinel) — chords never
lose notes to collisions, regardless of chord width.

Still retained: ties merge into sustains, open-string chug rule
(bypasses the cursor entirely), hammer_on/pull_off -> forced flip,
tap -> tap flag, sustain threshold + gap trim.
"""
from __future__ import annotations

import itertools
import logging
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

_RECENT_SHAPES = 4   # how many past chord shapes count as "recently used"
_TREND_WINDOW = 4    # how many past anchor pitches the direction trend spans

_logger = logging.getLogger(__name__)


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
    folds it into 0-4, which is what produces the staircase wrap.

    The `_last_group_*`/`_recent_*` fields extend this same persistent
    state to chords: they hold just enough context about recently emitted
    same-tick groups for `_rank_chord_shape` to judge motion/variety
    without re-deriving it from the full note history. They reset
    alongside the cursor so a fresh phrase never gets scored against a
    chord from a different musical idea.
    """

    def __init__(self) -> None:
        self._lane_cursor: int = 0
        self._last_pitch: int | None = None
        self._last_tick: int | None = None
        self._last_group_lanes: tuple[int, ...] | None = None
        self._last_group_pitches: tuple[int, ...] | None = None
        self._recent_group_lanes: list[tuple[int, ...]] = []
        self._recent_anchor_pitches: list[int] = []

    def reset(self) -> None:
        self._lane_cursor = 0
        self._last_pitch = None
        self._last_tick = None
        self._last_group_lanes = None
        self._last_group_pitches = None
        self._recent_group_lanes = []
        self._recent_anchor_pitches = []

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


def _chord_shape_candidates(k: int) -> list[tuple[int, ...]]:
    """All ways to place k distinct notes on the 5 physical lanes
    (0.._MAX_LANE), each candidate already ascending. Deliberately kept
    independent of scoring/history — this function only enumerates what's
    *legal* (there are only C(5,k) options, at most 10), never what's
    *preferred*; `_rank_chord_shape` handles preference. Returns `[]` for
    `k` outside 1..5 (5 physical lanes) — callers must fall back for
    `k > 5`, a pre-existing, documented, out-of-scope limitation."""
    if k < 1 or k > _MAX_LANE + 1:
        return []
    return list(itertools.combinations(range(_MAX_LANE + 1), k))


def _rank_chord_shape(
    candidate: tuple[int, ...],
    current_pitches: tuple[int, ...],
    prev_lanes: tuple[int, ...] | None,
    prev_pitches: tuple[int, ...] | None,
    recent_lanes: list[tuple[int, ...]],
    anchor_preferred_lane: int,
    direction: int,
) -> tuple[float, dict[str, float]]:
    """Rank one candidate chord shape. This expresses a PREFERENCE among
    several musically-legitimate options, not an objective correctness
    check (hence "rank", not "score") — there is no single right answer
    for how to lay a chord across 5 lanes. Returns `(total, breakdown)` so
    callers can log exactly which criteria fired, without duplicating this
    logic in a separate explain function.

    `direction` is -1/0/+1: whether the phrase's recent anchor-pitch trend
    is descending/flat/ascending (see `_assign_group_lanes`'s trend-window
    computation, not just the immediately previous pitch — a single
    passing dip in an otherwise-ascending run shouldn't read as a reversal).
    """
    breakdown: dict[str, float] = {}
    anchor_lane = candidate[0]
    content_changed = prev_pitches is not None and current_pitches != prev_pitches
    content_unchanged = prev_pitches is not None and current_pitches == prev_pitches

    # Continues the established staircase motion. anchor_preferred_lane is
    # already cursor % 5 (wraparound-correct), so matching it *is* the
    # wrap-aware continuation — this is where chord wraparound comes from.
    if direction != 0 and anchor_lane == anchor_preferred_lane:
        breakdown["anchor"] = 3.0

    # Registers as harmonically different from the previous shape, but
    # only when the chord's pitch content actually changed — a real
    # repeat must not be penalized for keeping its shape.
    if content_changed and prev_lanes is not None and set(candidate) != set(prev_lanes):
        breakdown["harmonic_change"] = 2.0

    # Avoids repeating a shape that was already pinned at the floor/
    # ceiling while the phrase is still actively moving that direction —
    # the concrete "Blue+Orange, Blue+Orange" flattening bug.
    if direction != 0:
        pinned_repeat = False
        if prev_lanes is not None:
            if direction > 0 and max(prev_lanes) == _MAX_LANE and max(candidate) == _MAX_LANE:
                pinned_repeat = True
            elif direction < 0 and min(prev_lanes) == 0 and min(candidate) == 0:
                pinned_repeat = True
        if not pinned_repeat:
            breakdown["unpinned"] = 2.0

    # A contiguous span reads more cleanly than a scattered one, all else
    # equal. Weighted well below the other criteria on purpose: adjacency
    # should win a shape a tie it already deserves on other grounds, not
    # systematically pull the algorithm back toward "chords are adjacent"
    # as a de facto rule (there is explicitly no such requirement).
    span = max(candidate) - min(candidate) + 1
    if span == len(candidate):
        breakdown["readable"] = 0.5

    # Small nudge against oscillating back onto a shape used a couple of
    # chords ago, independent of the exact-previous-repeat check below.
    if candidate in recent_lanes:
        breakdown["recent_repeat"] = -0.5

    # No musical justification for an identical shape when the chord's
    # content is meaningfully different.
    if content_changed and prev_lanes is not None and candidate == prev_lanes:
        breakdown["unjustified_repeat"] = -3.0

    # Abrupt jump contrary to the established direction. Wrap-vs-jump is
    # disambiguated using anchor_preferred_lane: if the cursor's own
    # wraparound logic expected a wrap right now, a lane-number decrease
    # while ascending (or increase while descending) is the *correct*
    # continuation, not a penalized jump.
    if prev_lanes is not None and direction != 0:
        wrap_expected = (
            (direction > 0 and anchor_preferred_lane < prev_lanes[0])
            or (direction < 0 and anchor_preferred_lane > prev_lanes[0])
        )
        raw_delta = anchor_lane - prev_lanes[0]
        contrary = (
            (direction > 0 and raw_delta < 0 and not wrap_expected)
            or (direction < 0 and raw_delta > 0 and not wrap_expected)
        )
        if contrary:
            breakdown["contrary_jump"] = -2.0

    # Derived, not a literal rubric line: when the chord's content is
    # truly unchanged, pull toward keeping the exact same shape. Without
    # this, a repeated multi-note chord has no criterion favoring
    # stability (the criteria above are all gated on direction != 0 or
    # content_changed), which would leave repeats to an unreliable tiebreak.
    if content_unchanged and prev_lanes is not None and candidate == prev_lanes:
        breakdown["stability"] = 3.0

    return sum(breakdown.values()), breakdown


def _assign_group_lanes(
    group: list[dict[str, Any]],
    chug_string: int | None,
    contour: _ContourTracker,
    ir_tick: int,
) -> dict[int, int]:
    """Distinct lane per note in a same-tick group. Open chugs pulled out
    first. Among the rest (the fretted notes):

    - A single fretted note is placed exactly as a lone note would be —
      the contour cursor's raw wraparound lane. This keeps single-note
      runs (the overwhelming majority of notes) byte-for-byte unchanged.
    - Two or more fretted notes (a chord) generalize that same staircase
      mechanism: the lowest-pitched note still advances the shared cursor
      (it's the "melody"/anchor note), and the full chord's lane-shape is
      chosen by ranking every legal placement — see `_rank_chord_shape`.
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

    if not fretted:
        return lanes

    if len(fretted) == 1:
        note = fretted[0]
        preferred = contour.raw_lane(note["pitch"] or 0, ir_tick)
        lane = _nearest_free_lane(preferred, taken)
        lanes[id(note)] = lane
        # Keep the trend window continuous across mixed chord/single-note
        # runs, without touching _last_group_*/_recent_group_lanes (those
        # track chord-to-chord shape comparisons specifically).
        contour._recent_anchor_pitches.append(note["pitch"] or 0)
        if len(contour._recent_anchor_pitches) > _TREND_WINDOW:
            contour._recent_anchor_pitches.pop(0)
        return lanes

    anchor_pitch = fretted[0]["pitch"] or 0
    current_pitches = tuple(n["pitch"] or 0 for n in fretted)

    trend_ref = contour._recent_anchor_pitches[0] if contour._recent_anchor_pitches else None
    direction = 0 if trend_ref is None else (anchor_pitch > trend_ref) - (anchor_pitch < trend_ref)

    anchor_preferred_lane = contour.raw_lane(anchor_pitch, ir_tick)

    if len(fretted) <= _MAX_LANE + 1:
        prev_lanes = contour._last_group_lanes
        prev_pitches = contour._last_group_pitches

        scored = [
            (
                *_rank_chord_shape(
                    c, current_pitches, prev_lanes, prev_pitches,
                    contour._recent_group_lanes, anchor_preferred_lane, direction,
                ),
                c,
            )
            for c in _chord_shape_candidates(len(fretted))
        ]

        _, _, winner = max(
            scored,
            key=lambda item: (item[0], -abs(item[2][0] - anchor_preferred_lane), tuple(-x for x in item[2])),
        )

        if _logger.isEnabledFor(logging.DEBUG):
            for total, breakdown, c in scored:
                _logger.debug(
                    "chord @ tick=%s candidate=%s rank=%.1f breakdown=%s%s",
                    ir_tick, c, total, breakdown, " <- chosen" if c == winner else "",
                )

        for note, lane in zip(fretted, winner):
            lanes[id(note)] = lane
        chosen_anchor_lane = winner[0]
    else:
        # k > 5: no room for a full legal-shape search (only 5 physical
        # lanes exist). Pre-existing, documented limitation — chain off
        # the anchor via nearest-free-lane, seeded only from lanes this
        # loop itself assigned (never from `lanes.values()`, where
        # OPEN_NOTE could leak in — that was the old placeholder's bug).
        chosen_anchor_lane = _nearest_free_lane(anchor_preferred_lane, taken)
        taken.add(chosen_anchor_lane)
        lanes[id(fretted[0])] = chosen_anchor_lane
        prev_lane = chosen_anchor_lane
        for note in fretted[1:]:
            lane = _nearest_free_lane(prev_lane, taken)
            taken.add(lane)
            lanes[id(note)] = lane
            prev_lane = lane

    # Resync the persistent cursor to the lane the chord actually used
    # (scoring may have picked a different anchor lane than the raw
    # cursor value for the sake of variety/readability) so a later single
    # note continues from there, not from the stale raw value.
    contour._lane_cursor += chosen_anchor_lane - anchor_preferred_lane

    contour._last_group_lanes = tuple(lanes[id(n)] for n in fretted)
    contour._last_group_pitches = current_pitches
    contour._recent_group_lanes.append(contour._last_group_lanes)
    if len(contour._recent_group_lanes) > _RECENT_SHAPES:
        contour._recent_group_lanes.pop(0)
    contour._recent_anchor_pitches.append(anchor_pitch)
    if len(contour._recent_anchor_pitches) > _TREND_WINDOW:
        contour._recent_anchor_pitches.pop(0)

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
