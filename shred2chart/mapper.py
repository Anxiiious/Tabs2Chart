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
- Phrase starts (section marker or rest >= 1 bar): `_plan_phrase_start_lanes`
  picks a starting lane before the phrase is emitted — full-section lookahead
  at section boundaries; local lookahead at bar lines for compact descending
  openings. Section chord-shape memory survives rests; exact-measure lane
  replay handles returning identical source measures later in the tab.

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

REDUCTION BEFORE PLACEMENT: two shapes are folded down before any of the
above runs, because they are one musical event that happens to touch
several strings, and charting a button per string misrepresents them:
- an all-open low rake -> a single OPEN note (`_is_open_chug`)
- a power chord, root/fifth/octave -> its two distinct voices
  (`_is_power_chord`)
Both were measured against a hand-charted reference for this repo's test
song: the power-chord test fired on 175 of 472 onsets with no false
positives, and removed 210 notes that the reference chart does not play.
Note that these are *reductions of the source group*, not scoring
preferences — once folded, the surviving voices go through the ordinary
anchor/shape path unchanged.

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
_MAX_CHORD_PITCHES = 3  # game plan rule 3: chords cap at 3 distinct notes for playability
FORCED_FLAG = 5
TAP_FLAG = 6

# Pitch classes, relative to the root, that a power chord is allowed to
# contain: the root itself (0, which also covers octave doublings) and the
# perfect fifth (7). A group whose classes fall entirely inside this set is
# one barred shape under one finger, not a three-note harmony — see
# `_is_power_chord`.
_POWER_CHORD_CLASSES = frozenset({0, 7})

_MAX_LANE = 4  # lanes are 0-4; OPEN_NOTE(7) lives outside this range
_REST_RESET_TICKS = IR_TICKS_PER_QUARTER * 4  # 1 bar
# Repeated phrase families must see their later turnaround too.  Sixteen onset
# groups reaches beyond a typical one-bar chug prefix without globally
# re-mapping the section.
_BAR_HEADROOM_LOOKAHEAD = 16  # local re-anchor only; section starts see the whole section
_RIFF_PREFIX_EVENTS = 4  # a repeated phrase must contain at least four exact note events

_RECENT_SHAPES = 4   # how many past chord shapes count as "recently used"
_TREND_WINDOW = 4    # how many past anchor pitches the direction trend spans

# _rank_chord_shape's weights, named so future playtest tuning is a constant
# edit here, not a hunt through the scoring logic. Unconfirmed heuristic
# values, same status as _interval_to_step's semitone-bucketing thresholds
# — expect these to move once real chord-bearing charts get playtested.
_WEIGHT_ANCHOR = 3.0              # matches the raw wraparound cursor position
_WEIGHT_HARMONIC_CHANGE = 2.0     # shape differs when the chord's content did
_WEIGHT_UNPINNED = 2.0            # doesn't repeat a floor/ceiling-pinned shape
_WEIGHT_READABLE = 0.5            # contiguous span
_WEIGHT_RECENT_REPEAT = -0.5      # matches a shape used a couple of chords ago
_WEIGHT_UNJUSTIFIED_REPEAT = -3.0  # exact previous shape, content changed
_WEIGHT_CONTRARY_JUMP = -2.0      # anchor moves against the established direction
_WEIGHT_STABILITY = 3.0           # exact previous shape, content unchanged
_WEIGHT_HARMONY_SPREAD = 4.0      # upper twin-guitar voice: retain a visibly wider two-note shape
_WEIGHT_HARMONY_TRIAD_SPREAD = 4.0  # dense twin-guitar wall: avoid adjacent three-button blocks
_CHORD_CHANGE_WINDOW_TICKS = IR_TICKS_PER_QUARTER * 4  # one 4/4 bar
_DENSE_CHORD_CHANGES = 2  # distinct chord changes in one bar enable expressive voicings

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


def _is_open_chug(group: list[dict[str, Any]], chug_string: int | None) -> bool:
    """True when every note struck at this tick is an open string and the
    group includes the lowest-tuned string — the rhythmic low chug that
    real charts write as a single open note, however many strings the
    player actually rakes.

    Deliberately independent of the power-chord test below: in drop tuning
    the open 6/5/4 rake *is* a power chord, but in standard tuning the same
    three open strings are a fourth stack, and both are still one open-note
    chug. Tuning decides the intervals; it should not decide whether an
    all-open rake reads as one hit.
    """
    if chug_string is None:
        return False
    if not any(note.get("string") == chug_string for note in group):
        return False
    return all(note.get("fret") == 0 for note in group)


def _is_power_chord(pitches: list[int]) -> bool:
    """True when three or more distinct pitches collapse onto just the root
    and its fifth — root/fifth/octave and its extensions.

    This is the one-finger barre across adjacent strings (in drop tuning,
    a single fret across strings 6/5/4). Musically and physically it is one
    event, so it belongs on two lanes, not three: charting each string
    separately turns a one-finger chug into a three-button chord and
    inflates the note count without adding anything to play.

    Requires three or more distinct pitches on purpose. A bare root+fifth
    dyad already occupies exactly two lanes, so there is nothing to
    collapse and this must not fire on it.
    """
    distinct = sorted(set(pitches))
    if len(distinct) < 3:
        return False
    root = distinct[0]
    return all((pitch - root) % 12 in _POWER_CHORD_CLASSES for pitch in distinct)


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
        self._riff_shapes: dict[tuple[int, ...], tuple[int, ...]] = {}
        self._recent_chord_change_ticks: list[int] = []

    def reset(self, initial_lane: int = 0, *, preserve_riff_shapes: bool = False) -> None:
        self._lane_cursor = initial_lane
        self._last_pitch = None
        self._last_tick = None
        self._last_group_lanes = None
        self._last_group_pitches = None
        self._recent_group_lanes = []
        self._recent_anchor_pitches = []
        self._recent_chord_change_ticks = []
        if not preserve_riff_shapes:
            self._riff_shapes = {}

    def raw_lane(self, pitch: int, ir_tick: int) -> int:
        """Advance the cursor for `pitch` and return its lane (0-4)."""
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
    harmony_voice: bool,
    expressive_progression: bool,
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

    This is a bounded local optimization — each chord is ranked only
    against the immediately previous shape and a short recent-shape
    history, never against future chords. That's a deliberate trade-off
    for determinism and O(1)-per-chord performance, not an oversight: a
    global search (look-ahead/backtracking over the whole phrase) could
    in principle avoid every non-adjacent repeat in a long run, but at
    real complexity cost for a readability difference unlikely to matter
    on an actual Clone Hero highway. See the module docstring's honest
    accounting of what this trade-off does and doesn't guarantee.
    """
    breakdown: dict[str, float] = {}
    anchor_lane = candidate[0]
    content_changed = prev_pitches is not None and current_pitches != prev_pitches
    content_unchanged = prev_pitches is not None and current_pitches == prev_pitches

    # Continues the established staircase motion. anchor_preferred_lane is
    # already cursor % 5 (wraparound-correct), so matching it *is* the
    # wrap-aware continuation — this is where chord wraparound comes from.
    if direction != 0 and anchor_lane == anchor_preferred_lane:
        breakdown["anchor"] = _WEIGHT_ANCHOR

    # Registers as harmonically different from the previous shape, but
    # only when the chord's pitch content actually changed — a real
    # repeat must not be penalized for keeping its shape.
    if content_changed and prev_lanes is not None and set(candidate) != set(prev_lanes):
        breakdown["harmonic_change"] = _WEIGHT_HARMONIC_CHANGE

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
            breakdown["unpinned"] = _WEIGHT_UNPINNED

    # A contiguous span reads more cleanly than a scattered one, all else
    # equal. Weighted well below the other criteria on purpose: adjacency
    # should win a shape a tie it already deserves on other grounds, not
    # systematically pull the algorithm back toward "chords are adjacent"
    # as a de facto rule (there is explicitly no such requirement).
    span = max(candidate) - min(candidate) + 1
    if span == len(candidate):
        breakdown["readable"] = _WEIGHT_READABLE

    # The blend stage marks notes chosen from a higher simultaneous guitar
    # voice. A wide dyad makes that audible register lift visible on the
    # highway (G/R -> R/B), rather than treating it as a new adjacent chord
    # (G/R -> R/Y).
    if (harmony_voice or expressive_progression) and len(candidate) == 2 and span == 3:
        breakdown["harmony_spread"] = _WEIGHT_HARMONY_SPREAD
    if (harmony_voice or expressive_progression) and len(candidate) == 3 and span == 4:
        breakdown["harmony_triad_spread"] = _WEIGHT_HARMONY_TRIAD_SPREAD

    # Small nudge against oscillating back onto a shape used a couple of
    # chords ago, independent of the exact-previous-repeat check below.
    if candidate in recent_lanes:
        breakdown["recent_repeat"] = _WEIGHT_RECENT_REPEAT

    # No musical justification for an identical shape when the chord's
    # content is meaningfully different.
    if content_changed and prev_lanes is not None and candidate == prev_lanes:
        breakdown["unjustified_repeat"] = _WEIGHT_UNJUSTIFIED_REPEAT

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
            breakdown["contrary_jump"] = _WEIGHT_CONTRARY_JUMP

    # Derived, not a literal rubric line: when the chord's content is
    # truly unchanged, pull toward keeping the exact same shape. Without
    # this, a repeated multi-note chord has no criterion favoring
    # stability (the criteria above are all gated on direction != 0 or
    # content_changed), which would leave repeats to an unreliable tiebreak.
    if content_unchanged and prev_lanes is not None and candidate == prev_lanes:
        breakdown["stability"] = _WEIGHT_STABILITY

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

    # An all-open low rake is one chug regardless of how many strings it
    # spans. The test is "every note in the group is open", not "only one
    # note was struck": a fretted note anywhere in the group means an open
    # string is ringing *under* a voicing, which is a real chord and still
    # goes through the fretted-note logic below.
    if _is_open_chug(group, chug_string):
        for note in group:
            lanes[id(note)] = OPEN_NOTE
        return lanes

    fretted = list(group)
    fretted.sort(key=lambda n: n["pitch"] or 0)

    # Collapse same-tick notes sharing a distinct pitch (octave/string
    # doubles) down to one representative, then cap at 3 distinct pitches
    # (game plan rule 3) - anything beyond that shares the lane of the
    # nearest kept pitch rather than inflating the chord shape.
    seen_pitches: dict[int, dict[str, Any]] = {}
    extras: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for note in fretted:
        pitch = note["pitch"] or 0
        if pitch in seen_pitches:
            extras.append((note, seen_pitches[pitch]))
        else:
            seen_pitches[pitch] = note

    kept = list(seen_pitches.values())
    kept.sort(key=lambda n: n["pitch"] or 0)

    # Fold a power chord down to its distinct voices before any shape is
    # chosen. The octave (and any further doubling) shares its root's lane
    # via the same `extras` mechanism that already handles unison doubles
    # on different strings, so the note survives into the chart and simply
    # stops claiming a button of its own.
    if _is_power_chord([note["pitch"] or 0 for note in kept]):
        root_pitch = kept[0]["pitch"] or 0
        voices: dict[int, dict[str, Any]] = {}
        for note in kept:
            pitch_class = ((note["pitch"] or 0) - root_pitch) % 12
            if pitch_class in voices:
                extras.append((note, voices[pitch_class]))
            else:
                voices[pitch_class] = note
        kept = sorted(voices.values(), key=lambda n: n["pitch"] or 0)

    if len(kept) > _MAX_CHORD_PITCHES:
        dropped = kept[_MAX_CHORD_PITCHES:]
        kept = kept[:_MAX_CHORD_PITCHES]
        # A same-pitch double recorded earlier can point at a representative
        # that is itself about to be capped. Re-home that double on the
        # highest kept pitch before assigning lanes, otherwise the later
        # extras pass dereferences a note that has no lane.
        dropped_ids = {id(note) for note in dropped}
        extras = [
            (note, kept[-1] if id(representative) in dropped_ids else representative)
            for note, representative in extras
        ]
        for note in dropped:
            extras.append((note, kept[-1]))

    fretted = kept

    if not fretted:
        return lanes

    if len(fretted) == 1:
        note = fretted[0]
        preferred = contour.raw_lane(note["pitch"] or 0, ir_tick)
        lane = _nearest_free_lane(preferred, taken)
        lanes[id(note)] = lane
        for extra, representative in extras:
            lanes[id(extra)] = lanes[id(representative)]
        # Keep the trend window continuous across mixed chord/single-note
        # runs, without touching _last_group_*/_recent_group_lanes (those
        # track chord-to-chord shape comparisons specifically).
        contour._recent_anchor_pitches.append(note["pitch"] or 0)
        if len(contour._recent_anchor_pitches) > _TREND_WINDOW:
            contour._recent_anchor_pitches.pop(0)
        return lanes

    anchor_pitch = fretted[0]["pitch"] or 0
    current_pitches = tuple(n["pitch"] or 0 for n in fretted)
    harmony_voice = any(note.get("harmony_voice") for note in fretted)

    trend_ref = contour._recent_anchor_pitches[0] if contour._recent_anchor_pitches else None
    direction = 0 if trend_ref is None else (anchor_pitch > trend_ref) - (anchor_pitch < trend_ref)

    anchor_preferred_lane = contour.raw_lane(anchor_pitch, ir_tick)

    # Dense chord progressions deserve expressive shapes, whereas an
    # isolated chord change should not abruptly turn into a wide reach.
    # Keep a one-bar rolling history of *distinct* chord changes; repeated
    # chugs deliberately add nothing here and preserve their established
    # lane shape.
    if contour._last_group_pitches is not None and current_pitches != contour._last_group_pitches:
        contour._recent_chord_change_ticks.append(ir_tick)
    contour._recent_chord_change_ticks = [
        tick for tick in contour._recent_chord_change_ticks
        if tick >= ir_tick - _CHORD_CHANGE_WINDOW_TICKS
    ]
    expressive_progression = len(contour._recent_chord_change_ticks) >= _DENSE_CHORD_CHANGES

    if len(fretted) <= _MAX_LANE + 1:
        prev_lanes = contour._last_group_lanes
        prev_pitches = contour._last_group_pitches

        cached_shape = contour._riff_shapes.get(current_pitches)
        if cached_shape is not None and len(cached_shape) == len(fretted):
            winner = cached_shape
            scored = []
        else:
            scored = [
                (
                    *_rank_chord_shape(
                        c, current_pitches, prev_lanes, prev_pitches,
                        contour._recent_group_lanes, anchor_preferred_lane, direction,
                        harmony_voice, expressive_progression,
                    ),
                    c,
                )
                for c in _chord_shape_candidates(len(fretted))
            ]

            _, _, winner = max(
                scored,
                key=lambda item: (item[0], -abs(item[2][0] - anchor_preferred_lane), tuple(-x for x in item[2])),
            )

        if cached_shape is not None and _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("chord @ tick=%s reuses section riff shape=%s", ir_tick, winner)
        elif _logger.isEnabledFor(logging.DEBUG):
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
    # note continues from there, not from the stale raw value. This makes
    # the emitted lane authoritative for future calculations, not just a
    # display-time correction of the raw cursor.
    #
    # This does not accumulate: `chosen_anchor_lane` and
    # `anchor_preferred_lane` are both already-wrapped values in 0-4, so
    # the delta applied here is bounded to [-4, 4] on every single chord,
    # not a running error term that grows over a long solo. Each resync
    # is a one-time, bounded correction reflecting one real choice: the
    # next `raw_lane()` call reads out `cursor % 5`, which is exactly
    # `chosen_anchor_lane` plus whatever the next pitch's own interval
    # step adds — same mechanism as a lone note, no compounding drift.
    # The existing reset triggers (section marker, rest >= 1 bar) already
    # provide the periodic hard boundary a from-scratch design would add.
    contour._lane_cursor += chosen_anchor_lane - anchor_preferred_lane

    for extra, representative in extras:
        lanes[id(extra)] = lanes[id(representative)]

    contour._last_group_lanes = tuple(lanes[id(n)] for n in fretted)
    contour._last_group_pitches = current_pitches
    contour._riff_shapes.setdefault(current_pitches, contour._last_group_lanes)
    contour._recent_group_lanes.append(contour._last_group_lanes)
    if len(contour._recent_group_lanes) > _RECENT_SHAPES:
        contour._recent_group_lanes.pop(0)
    contour._recent_anchor_pitches.append(anchor_pitch)
    if len(contour._recent_anchor_pitches) > _TREND_WINDOW:
        contour._recent_anchor_pitches.pop(0)

    return lanes


def _plan_phrase_start_lanes(
    groups: dict[int, list[dict[str, Any]]],
    section_ticks: set[int],
    bar_ticks: set[int],
) -> dict[int, tuple[int, bool]]:
    """Choose each phrase's starting lane before emitting its notes.

    Section/rest starts use the whole remaining phrase, because Guitar Pro
    gives us the complete tab before we emit any lane. This avoids spending
    all of the highway at a phrase opening and discovering a needless wrap
    several measures later. A regular bar-line re-anchor remains deliberately
    local: it only catches a compact descending figure that has no explicit
    rest or section boundary.
    """
    ticks = sorted(groups)
    if not ticks:
        return {}

    hard_starts = [ticks[0]]
    section_starts = {ticks[0]}
    for previous, current in zip(ticks, ticks[1:]):
        crossed_section = any(previous < marker <= current for marker in section_ticks)
        if crossed_section or current - previous >= _REST_RESET_TICKS:
            hard_starts.append(current)
            if crossed_section:
                section_starts.add(current)

    starts = list(hard_starts)

    # A bar line may start a compact descending figure without a literal
    # rest or GP section marker. Re-anchor only when its *opening* contour
    # descends and never rises; ordinary bar boundaries remain continuous.
    for start in ticks:
        if start not in bar_ticks or start in starts:
            continue
        anchors = [
            min(note["pitch"] or 0 for note in groups[tick])
            for tick in ticks[ticks.index(start):ticks.index(start) + _BAR_HEADROOM_LOOKAHEAD]
        ]
        offsets = [0]
        for previous, current in zip(anchors, anchors[1:]):
            step = _interval_to_step(current - previous)
            offsets.append(offsets[-1] + (step if current > previous else -step if current < previous else 0))
        if min(offsets) < 0 and max(offsets) <= 0:
            starts.append(start)

    starts.sort()

    plans: dict[int, tuple[int, bool]] = {}
    for start in starts:
        if start in hard_starts:
            end = next((boundary for boundary in hard_starts if boundary > start), None)
            phrase_ticks = [tick for tick in ticks if start <= tick and (end is None or tick < end)]
        else:
            start_index = ticks.index(start)
            phrase_ticks = ticks[start_index:start_index + _BAR_HEADROOM_LOOKAHEAD]
        anchors = [
            min(note["pitch"] or 0 for note in groups[tick])
            for tick in phrase_ticks
        ]
        offset = 0
        minimum = maximum = 0
        for previous, current in zip(anchors, anchors[1:]):
            step = _interval_to_step(current - previous)
            offset += step if current > previous else -step if current < previous else 0
            minimum = min(minimum, offset)
            maximum = max(maximum, offset)

        legal = [lane for lane in range(_MAX_LANE + 1) if lane + minimum >= 0 and lane + maximum <= _MAX_LANE]
        if legal:
            lane = min(legal, key=lambda lane: abs(lane))
        else:
            # A phrase wider than five lanes must eventually wrap. Start at
            # the least-overflowing position, preferring lower lanes on ties.
            lane = min(
                range(_MAX_LANE + 1),
                key=lambda lane: (
                    max(0, -(lane + minimum)) + max(0, lane + maximum - _MAX_LANE),
                    lane,
                ),
            )
        # A rest resets contour motion, but not the section's established
        # riff vocabulary. Only a true section marker starts a new cache.
        plans[start] = (lane, start not in section_starts)
    return plans


def _group_fingerprint(group: list[dict[str, Any]]) -> tuple[tuple[int | None, int | None, int | None], ...]:
    """Exact played notes for one onset, independent of their chart lanes."""
    return tuple(sorted((note.get("string"), note.get("fret"), note.get("pitch")) for note in group))


def _find_repeated_measure_sources(
    groups: dict[int, list[dict[str, Any]]],
    bar_ticks: list[int] | None,
) -> dict[int, int]:
    """Map later identical source measures to the first matching measure.

    Each fingerprint contains every fretted-note onset and its offset within
    the measure. A repeated complete bar therefore replays the lane pattern
    already established by its earlier appearance, without guessing that a
    partial note run is the same musical idea.
    """
    if not bar_ticks:
        return {}
    starts = sorted(set(bar_ticks))
    sources: dict[int, int] = {}
    first_measures: dict[tuple, tuple[int, list[int]]] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else float("inf")
        ticks = [tick for tick in sorted(groups) if start <= tick < end]
        if not ticks:
            continue
        fingerprint = tuple((tick - start, _group_fingerprint(groups[tick])) for tick in ticks)
        original = first_measures.get(fingerprint)
        if original is None:
            first_measures[fingerprint] = (start, ticks)
            continue
        _, original_ticks = original
        if len(original_ticks) != len(ticks):
            continue
        for current_tick, original_tick in zip(ticks, original_ticks):
            sources[current_tick] = original_tick
    return sources


def _find_repeated_phrase_sources(
    groups: dict[int, list[dict[str, Any]]],
    section_ticks: list[int] | None,
    bar_ticks: list[int] | None,
) -> tuple[dict[int, int], dict[int, int]]:
    """Find exact repeated phrase prefixes within one named section.

    This complements whole-measure memory for cases where a riff repeats its
    opening chugs but changes at the tail (for example, a later lower-fret
    turnaround). It never crosses a section boundary and requires four
    matching fretted-note events with matching internal rhythm.
    """
    ticks = sorted(groups)
    width = _RIFF_PREFIX_EVENTS
    if len(ticks) < width * 2:
        return {}, {}
    boundaries = sorted(section_ticks or [])
    bar_set = set(bar_ticks or [])

    def section_at(tick: int) -> int:
        return sum(boundary <= tick for boundary in boundaries)

    fingerprints = [_group_fingerprint(groups[tick]) for tick in ticks]

    def window_at(start: int) -> tuple:
        return tuple(
            (fingerprints[index], ticks[index + 1] - ticks[index] if index < start + width - 1 else None)
            for index in range(start, start + width)
        )

    def offset_bounds(start: int) -> tuple[int, int]:
        # A phrase family at a bar head is planned against that bar's own
        # continuation.  Crossing into the following measure would let an
        # unrelated new figure distort this phrase's initial anchor.
        bar_end = next((bar for bar in sorted(bar_set) if bar > ticks[start]), None)
        limit = min(start + _BAR_HEADROOM_LOOKAHEAD, len(ticks))
        if bar_end is not None:
            limit = min(
                limit,
                next((index for index in range(start, len(ticks)) if ticks[index] >= bar_end), len(ticks)),
            )
        anchors = [
            min(note["pitch"] or 0 for note in groups[ticks[index]])
            for index in range(start, limit)
        ]
        offset = minimum = maximum = 0
        for previous, current in zip(anchors, anchors[1:]):
            step = _interval_to_step(current - previous)
            offset += step if current > previous else -step if current < previous else 0
            minimum = min(minimum, offset)
            maximum = max(maximum, offset)
        return minimum, maximum

    def lane_for_bounds(minimum: int, maximum: int) -> int:
        legal = [lane for lane in range(_MAX_LANE + 1) if lane + minimum >= 0 and lane + maximum <= _MAX_LANE]
        if legal:
            return min(legal, key=lambda lane: abs(lane))
        return min(
            range(_MAX_LANE + 1),
            key=lambda lane: (
                max(0, -(lane + minimum)) + max(0, lane + maximum - _MAX_LANE),
                lane,
            ),
        )

    sources: dict[int, int] = {}
    matches: list[tuple[int, int]] = []
    first_windows: dict[tuple[int, tuple], int] = {}
    for start in range(len(ticks) - width + 1):
        if bar_set and ticks[start] not in bar_set:
            continue
        key = (section_at(ticks[start]), window_at(start))
        original = first_windows.setdefault(key, start)
        if original == start or start < original + width:
            continue
        length = width
        while (
            original + length < len(ticks)
            and start + length < len(ticks)
            and section_at(ticks[original + length]) == section_at(ticks[start + length])
            and fingerprints[original + length] == fingerprints[start + length]
            and (original + length == len(ticks) - 1 or start + length == len(ticks) - 1
                 or ticks[original + length + 1] - ticks[original + length]
                 == ticks[start + length + 1] - ticks[start + length])
        ):
            length += 1
        for offset in range(length):
            sources.setdefault(ticks[start + offset], ticks[original + offset])
        matches.append((original, start))

    # Plan against the closest repeated occurrence.  A phrase can recur again
    # much later in a section with a genuinely different continuation; that
    # must not force the original chug prefix into an artificial compromise.
    # The immediate variation is the useful evidence for the local anchor.
    anchor_bounds: dict[int, tuple[int, int]] = {}
    closest_matches: dict[int, int] = {}
    for original, current in matches:
        closest_matches.setdefault(original, current)
    for original, current in closest_matches.items():
        original_bounds = offset_bounds(original)
        current_bounds = offset_bounds(current)
        existing = anchor_bounds.get(ticks[original], original_bounds)
        anchor_bounds[ticks[original]] = (
            min(existing[0], original_bounds[0], current_bounds[0]),
            max(existing[1], original_bounds[1], current_bounds[1]),
        )
    return sources, {
        tick: lane_for_bounds(minimum, maximum)
        for tick, (minimum, maximum) in anchor_bounds.items()
    }


def map_notes(
    ir_notes: list[dict[str, Any]],
    section_ticks: list[int] | None = None,
    bar_ticks: list[int] | None = None,
) -> list[ChartNote]:
    notes = _merge_ties(ir_notes)
    chug_string = _lowest_tuning_string(notes)
    groups: dict[int, list[dict[str, Any]]] = {}
    for note in notes:
        groups.setdefault(note["tick"], []).append(note)

    phrase_starts = _plan_phrase_start_lanes(
        groups, set(section_ticks or []), set(bar_ticks or []),
    )
    measure_sources = _find_repeated_measure_sources(groups, bar_ticks)
    phrase_sources, phrase_anchor_lanes = _find_repeated_phrase_sources(
        groups, section_ticks, bar_ticks,
    )
    replay_sources = {**phrase_sources, **measure_sources}
    contour = _ContourTracker()
    emitted_lanes: dict[int, list[int]] = {}

    chart_notes: list[ChartNote] = []
    for ir_tick, group in sorted(groups.items()):
        if ir_tick in phrase_starts:
            initial_lane, preserve_riff_shapes = phrase_starts[ir_tick]
            contour.reset(initial_lane, preserve_riff_shapes=preserve_riff_shapes)
        if ir_tick in phrase_anchor_lanes:
            contour.reset(phrase_anchor_lanes[ir_tick], preserve_riff_shapes=True)

        lane_by_id = _assign_group_lanes(group, chug_string, contour, ir_tick)
        duration = max(n["duration_ticks"] for n in group)
        lanes = emitted_lanes.get(replay_sources.get(ir_tick), sorted(set(lane_by_id.values())))
        emitted_lanes[ir_tick] = lanes
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
