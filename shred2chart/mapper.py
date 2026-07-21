"""IR notes -> Clone Hero 5-lane note events (Stage 4, M4 contour mapping).

- **Single notes are grouped into hand positions, then rank-ordered
  within each group** (see `_group_into_hand_positions`/
  `_hand_position_lanes`): consecutive notes within `HAND_POSITION_SEMITONES`
  of each other form one group (a real guitarist doesn't shift hand
  position for every note); within a group, DISTINCT pitches are ranked
  by pitch and spread across consecutive lanes centered on the group's
  target lane, so every genuinely different fret gets its own lane
  rather than being rounded into a shared bucket. This replaced an
  earlier per-note proportional-distance formula (v1-v3; see
  SHRED2CHART_GAMEPLAN.md's 2026-07-19 entries) after a real playtest
  found it still clustered a descending chug run (7-5-4-5-4-2 frets)
  onto just 2 lanes - the rank-order-within-group design fixes this by
  construction: it looks at the WHOLE group's distinct pitches at once,
  not one proportional step at a time.
- **Between groups, the next group's target lane comes from a
  proportional leap** from the previous group's anchor (same
  `SEMITONES_PER_LANE` formula as v2), always re-centering to
  `CENTER_LANE` afterward so the next group has headroom on both sides
  (same rationale as v3's error-leak/re-center fixes).
- **A same-lane collision at a group boundary is nudged away** (the
  group's lanes shift by 1, preserving its internal rank spacing) when
  the first note of a new group would otherwise land on the exact same
  lane as the last note of the previous group despite a different pitch
  - confirmed against 4 real HOPO collisions and a real picked
  descending chug run in "Still Searching" track 1 (v4/v5).
- **Exact-repeat hand-position groups are memoized and replayed
  verbatim**: the first time an exact sequence of pitches is seen as one
  group, its final (post-nudge) lane sequence is cached; every later
  occurrence of that exact same pitch sequence reuses the cached lanes
  outright, skipping fresh leap/nudge computation entirely. This is
  necessary because real songs commonly reprise earlier material across
  section boundaries (e.g. a chorus riff returning in a later section)
  - recomputing from scratch each time can drift by a constant lane
  offset depending on what happens to precede that occurrence, breaking
  the "same riff always looks the same" property. Confirmed against
  real repeats in "Still Searching" track 1: a 40-note riff recurs
  between sections [C]/[D] and the [A'] reprise; a wide lead lick recurs
  between [E] and [A'] many times. Memoization is intentionally
  unconditional (never re-nudged on replay) even on the rare occasion a
  memoized group's first note would otherwise collide with whatever
  precedes it that particular time - "the same riff looks the same every
  time" was judged more important than avoiding that rarer, smaller
  collision (2 such cases found in the real file).
- **Ties merge into sustains** (the EOF-confirmed behavior): a note with
  `tied: True` extends the previous note at the same string+pitch
  instead of becoming a new attack.
- **Open-string chugs -> open note (N 7)**: fret 0 on the track's
  lowest-tuned string. The tuning is inferred from the notes themselves
  (pitch - fret = the string's tuning), so drop tunings work without
  any tuning metadata.
- **Technique flags**: hammer_on/pull_off -> forced flip (`N 5`),
  tap -> tap modifier (`N 6`, which overrides HOPO per the spec).

Chord voicing uses the SAME grouping/rank-order/memoize design as single
notes (see `_group_chords_into_hand_positions`/`_rank_order_chord_roots`/
`_chord_lanes_sequence`): chords are grouped by root pitch into hand
positions the same way single notes are - but with a ROLLING anchor
(compare each chord's root to the PREVIOUS chord's root, not the
group's first root), since a gradual chord-progression walk (every step
small, but the group's start and end far apart) needs to stay one group
for the rank-order spread below to see the whole progression at once
(confirmed necessary on a real progression, "Still Searching" track 1
section [F]: roots 56-57-59-61-62, every step <=4 semitones but a total
span of 6 - a fixed-first-root anchor split this partway through). A
group's DISTINCT chord roots are then rank-ordered and spread evenly
across the lane range still available after reserving room for the
widest chord's own internal voicing - not each chord independently
computing a leap off the previous one. An earlier per-chord leap+memo
design (keyed on absolute root-to-root leaps, analogous to a v1-era
single-note contour) crowded several nearby-but-distinct chord shapes
into the same 1-2 lanes, because whichever shape happened to memoize
first in a fast chord progression "claimed" a spot the later shapes'
leap math kept landing near too. Within one chord, its own distinct
pitches are laid out starting from its rank-assigned base lane, spaced
by harmonic interval (`_interval_to_gap`, ported from the Copilot/Fable
M4 chord-spacing redesign): tight intervals (m2-M3) stay adjacent, wider
ones (P4/P5, i.e. power chords, and beyond) spread across skipped lanes
- so a power chord reads as visibly wider than a tight cluster instead
of both collapsing to the same adjacent-lane shape - capped at 3 lanes
wide (game plan rule 3's cap). Exact-repeat chord shapes are memoized
and replayed verbatim, same rationale as single notes.

Tick conversion: IR is 960 ticks/quarter (PyGuitarPro convention),
.chart is emitted at Resolution=192, so every position/length divides
by 5 (all common note values stay exact integers).

Note-type semantics (N 0-4 lanes, 5 forced, 6 tap, 7 open) are pinned
from the community chart-format docs (TheNathannator's
GuitarGame_ChartFormats), not from memory, per the game plan's mandate.
"""
from __future__ import annotations

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

# Consecutive single notes within this many semitones of each other form
# one "hand position" group (roughly a real guitarist's 4-5 fret span);
# a bigger jump starts a new group (see _group_into_hand_positions).
HAND_POSITION_SEMITONES = 4

# How many semitones of pitch movement equal one lane step, for the
# proportional leap BETWEEN groups (not within one - see
# _hand_position_lanes for the within-group rank-order rule).
SEMITONES_PER_LANE = 3

def _interval_to_gap(semitones: int) -> int:
    """Map a chord interval (semitones) to a chart lane gap (1-4).

    Tight intervals (m2/M2/m3/M3) stay on close lanes; wider ones
    (P4/P5 - i.e. power chords) and beyond spread across skipped
    lanes, rather than always sitting adjacent. Ported from the
    Copilot/Fable M4 chord-spacing redesign: adjacent-only spacing made
    power chords (root+P5) look identical to tight clusters (root+m2),
    losing the harmonic-width information a player relies on to read
    chord shapes at a glance.
    """
    semitones = abs(semitones) % 12
    if semitones <= 4:   # m2, M2, m3, M3
        return 1
    if semitones <= 7:   # P4, P5 (power chords land here)
        return 2
    if semitones <= 9:   # m6, M6
        return 3
    return 4              # m7, M7, octave

# The target lane a group's rank-order spread is centered on, by default
# (the very first group, and after any leap - see _hand_position_lanes).
# Centering rather than an absolute-pitch-derived lane means a group has
# headroom on both sides regardless of how many distinct pitches it
# turns out to contain.
CENTER_LANE = 2


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


def _chord_offsets(pitches: tuple[int, ...]) -> list[int]:
    """Lane offsets (from the root's lane) for a chord's DISTINCT pitches
    (capped to the lowest 3, per game plan rule 3), spaced by harmonic
    interval via `_interval_to_gap` rather than always-adjacent: a power
    chord (root+P5) should look wider on the neck than a tight cluster
    (root+m2), so a reader can tell chord shapes apart at a glance. Spans
    over 4 lanes are proportionally compressed back to fit (root and
    tightest-fit outer note pinned to 0 and 4).

    A 3rd distinct pitch only ever adds 1 more lane beyond the root-to-2nd
    gap, never a further full gap - confirmed against a real charter
    (Angevil, "Sick or Sane"): every 3-4 note power-chord voicing there
    (root+5th, plus octave doublings) is charted as a 2-lane shape, not
    the 4-lane spread the uncapped per-interval formula would produce -
    real charts read a chord's 3rd+ note as "one more button", not
    another full harmonic gap."""
    distinct = sorted(set(pitches))[:3]
    if len(distinct) == 1:
        return [0]
    offsets = [0, _interval_to_gap(distinct[1] - distinct[0])]
    if len(distinct) == 3:
        offsets.append(offsets[-1] + 1)
    span = offsets[-1]
    if span > 4:
        if len(distinct) == 2:
            offsets = [0, 4]
        else:
            mid = max(1, min(3, round(offsets[1] * 4 / span)))
            offsets = [0, mid, 4]
    return offsets


def _chord_width_lanes(pitches: tuple[int, ...]) -> int:
    """How many lanes (beyond the root's own lane) a chord's own internal
    voicing needs - the widest span its distinct pitches (capped to 3,
    per game plan rule 3) require, per the interval-spread gap rule."""
    return _chord_offsets(pitches)[-1]


def _chord_internal_lanes(pitches: tuple[int, ...], base_lane: int) -> dict[int, int]:
    """Lay a chord's DISTINCT pitches (sorted) starting at base_lane, one
    interval-spread gap per adjacent pair (see `_interval_to_gap`). Capped
    to the lowest 3 distinct pitches - any pitch beyond that has no entry
    in the returned map."""
    distinct = sorted(set(pitches))[:3]
    offsets = _chord_offsets(pitches)
    return {pitch: base_lane + offset for pitch, offset in zip(distinct, offsets)}


def _group_chords_into_hand_positions(
    chords: list[tuple[int, ...]]
) -> list[list[tuple[int, ...]]]:
    """Split a sequence of chords into runs where each chord's root stays
    within HAND_POSITION_SEMITONES of the PREVIOUS chord's root - a
    rolling comparison, not a fixed first-root anchor like
    _group_into_hand_positions, so a gradual chord-progression walk
    (each step small, but the group's start and end far apart) stays one
    group instead of splitting partway through. Confirmed necessary on a
    real chord progression ("Still Searching" track 1 section [F]:
    roots 56-57-59-61-62, every step <=4 semitones but the first-to-last
    span is 6) - a fixed-first-root anchor split this into two groups
    partway through, which meant the rank-order-across-the-group fix
    only saw half the progression's roots at once and still crowded
    several distinct chords onto the same 1-2 lanes.

    A group is also cut off once it has accumulated as many distinct
    roots as there is room for (5 lanes, minus whatever the group's
    widest chord's own internal voicing needs to reserve) - a rolling
    small-step anchor can otherwise "creep" a group across a much wider
    span than any single hand position actually covers, packing more
    distinct roots into the group than can possibly get distinct base
    lanes (confirmed on a real progression, "Still Searching" track 1
    section [H]: roots walk 54-57-59-61 then creep back down through
    53-56-52-51, each step <=4 semitones but 7 distinct roots end up
    needing to share a span that a 2-note chord's own width has already
    narrowed to 4 lanes - rank-ordering by position alone then maps
    several genuinely different roots onto the same lane pair, which
    reads as "missed chord changes")."""
    groups: list[list[tuple[int, ...]]] = []
    current: list[tuple[int, ...]] = []
    current_roots: set[int] = set()
    current_max_width = 0
    prev_root: int | None = None
    for chord in chords:
        root = chord[0]
        width = _chord_width_lanes(chord)
        next_max_width = max(current_max_width, width)
        next_distinct = len(current_roots | {root})
        max_distinct_roots = 5 - next_max_width
        would_overflow = next_distinct > max_distinct_roots
        if prev_root is None or abs(root - prev_root) > HAND_POSITION_SEMITONES or would_overflow:
            if current:
                groups.append(current)
            current = [chord]
            current_roots = {root}
            current_max_width = width
        else:
            current.append(chord)
            current_roots.add(root)
            current_max_width = next_max_width
        prev_root = root
    if current:
        groups.append(current)
    return groups


def _rank_order_chord_roots(
    group: list[tuple[int, ...]], center_lane: int
) -> dict[int, int]:
    """Rank a hand-position group's DISTINCT chord roots and spread them
    evenly across the lane range still available after reserving room
    for the widest chord's own internal voicing, centered on center_lane
    - so a cluster of nearby, genuinely different chord shapes (e.g. a
    fast chord-progression run) spreads across the neck instead of each
    independently computing a leap that can crowd several shapes into
    the same corner (confirmed against a real chord progression, "Still
    Searching" track 1 section [F]: 5 nearby power-chord roots all
    landed on lanes 3-4 under the per-chord leap+memo design, since
    several of them happened to memoize high on first occurrence)."""
    distinct_roots = sorted({chord[0] for chord in group})
    max_width = max(_chord_width_lanes(chord) for chord in group)
    available_span = 4 - max_width
    if len(distinct_roots) == 1:
        return {distinct_roots[0]: max(0, min(available_span, center_lane))}
    # Proportional rank position across the available span. Safe from
    # collisions/overflow by construction: _group_chords_into_hand_positions
    # never lets a group accumulate more distinct roots than
    # available_span + 1 can hold (see its docstring), so this plain
    # round() always lands on distinct, in-range lanes - no post-hoc
    # de-duping needed.
    last = len(distinct_roots) - 1
    return {
        root: round(i * available_span / last)
        for i, root in enumerate(distinct_roots)
    }


def _chord_lanes_sequence(chords: list[tuple[int, ...]]) -> list[list[int]]:
    """Lane for each chord in a track, in order. Nearby chords (by root,
    within one hand position) are grouped and rank-ordered together, the
    same way _single_note_lanes rank-orders nearby single notes - see
    _rank_order_chord_roots and the module docstring. BETWEEN groups, the
    next group's center lane comes from the same proportional-leap
    formula as _single_note_lanes (off the previous group's LAST root,
    since that's the hand position the player is actually coming from),
    instead of every new group resetting to CENTER_LANE regardless of
    where the song was already sitting on the neck - without this, a
    group with only one distinct root (common: a riff sits on one power
    chord for a while) always collapsed back to the same lane pair no
    matter how far the song had actually moved, which is why real chord
    progressions read as "stuck near green/red" even across big root
    jumps between sections. Exact-repeat chord shapes are memoized and
    replayed verbatim WITHIN one hand-position group (real riffs repeat
    a shape many times in a row) - but the memo is scoped PER GROUP, not
    global across the whole song: a global memo would let whichever
    group happens to see a given chord shape first permanently lock its
    lane for every later, unrelated occurrence, silently overriding that
    later group's own rank-order spread (confirmed on a real
    progression, "Still Searching" track 1 section [F]: chord (59,71)
    first appears early in an unrelated group and memoizes to lanes
    [3,4]; a much later group with roots 56-57-59-61 computes 59's local
    rank-order lane as [2,3], but the global memo intercepted it first -
    same for (61,73) and (62,74) - so 30 consecutive, genuinely
    different chords all rendered as the same blue/orange shape)."""
    groups = _group_chords_into_hand_positions(chords)
    lanes_seq: list[list[int]] = []
    prev_group_last_root: int | None = None

    for group in groups:
        if prev_group_last_root is None:
            center_lane = CENTER_LANE
        else:
            delta = group[0][0] - prev_group_last_root
            lane_delta = round(delta / SEMITONES_PER_LANE)
            if lane_delta == 0:
                lane_delta = 1 if delta > 0 else (-1 if delta < 0 else 0)
            center_lane = max(0, min(4, CENTER_LANE + lane_delta))
        base_lane_by_root = _rank_order_chord_roots(group, center_lane)
        group_memo: dict[tuple[int, ...], list[int]] = {}
        for chord in group:
            cached = group_memo.get(chord)
            if cached is not None:
                lanes_seq.append(cached)
                continue
            base_lane = base_lane_by_root[chord[0]]
            lane_map = _chord_internal_lanes(chord, base_lane)
            chord_lanes = [lane_map[p] for p in chord if p in lane_map]
            group_memo[chord] = chord_lanes
            lanes_seq.append(chord_lanes)
        prev_group_last_root = group[-1][0]
    return lanes_seq


def _group_into_hand_positions(notes: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Split a sequence of (pitch, fret) single notes into runs where
    every note stays within HAND_POSITION_SEMITONES of the PREVIOUS
    note's FRET (a rolling anchor, not a fixed first-note anchor - same
    rationale as the chord grouping's rolling anchor: a gradual walk
    should stay one group even if its start and end are far apart).

    Grouped by FRET, not pitch: a real guitarist's hand position is a
    fret-span on the neck, not a pitch interval - the same fret on a
    lower string sounds many semitones apart from a higher string, but
    costs the hand nothing to reach (confirmed on a real lead lick,
    "Still Searching" track 1 ticks 92000-110880: pitch 61 - a low pedal
    tone on string 3, fret 11 - alternates against a moving voice on
    strings 4-6, frets 9-15; every pitch delta between them is >4
    semitones, up to 17, but the whole phrase sits in a tight 6-fret
    span. Grouping by pitch fragmented this into ~20 one-note groups,
    each leaping independently from a reset center lane, so several
    different high notes collapsed onto the same clamped lane and the
    phrase read as only two buttons repeating).

    A group is also cut off once it has accumulated as many distinct
    pitches as there is room for (5 lanes), so _rank_order_lanes always
    has enough lanes to keep every distinct pitch in the group
    separate."""
    groups: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    current_distinct: set[int] = set()
    prev_fret: int | None = None
    for note in notes:
        pitch, fret = note
        next_distinct = len(current_distinct | {pitch})
        would_overflow = pitch not in current_distinct and next_distinct > 5
        if prev_fret is None or abs(fret - prev_fret) > HAND_POSITION_SEMITONES or would_overflow:
            if current:
                groups.append(current)
            current = [note]
            current_distinct = {pitch}
        else:
            current.append(note)
            current_distinct.add(pitch)
        prev_fret = fret
    if current:
        groups.append(current)
    return groups


def _rank_order_lanes(group: list[int], center_lane: int) -> list[int]:
    """Rank the group's DISTINCT pitches and spread them across
    consecutive lanes centered on center_lane, so every genuinely
    different pitch in the group gets its own lane (not rounded into a
    shared bucket by a per-note proportional formula)."""
    distinct = sorted(set(group))
    spread = min(4, len(distinct) - 1) if len(distinct) > 1 else 0
    lo_lane = max(0, min(4 - spread, center_lane - spread // 2))
    rank_to_lane = {pitch: lo_lane + i for i, pitch in enumerate(distinct)}
    return [rank_to_lane[pitch] for pitch in group]


def _single_note_lanes(notes: list[tuple[int, int]]) -> list[int]:
    """Lane for each single (pitch, fret) note in a track, in order. See
    the module docstring for the full rank-order + leap + memoize
    design. Grouping uses fret (hand position); lane rank-ordering
    within/between groups uses pitch (melodic contour) - see
    _group_into_hand_positions."""
    groups = _group_into_hand_positions(notes)
    lanes: list[int] = []
    anchor_lane = CENTER_LANE
    prev_group_first_pitch: int | None = None
    prev_group_last_pitch: int | None = None
    prev_group_last_lane: int | None = None
    # Exact-repeat memo: same pitch sequence -> same final lane sequence,
    # regardless of what happens to precede it this time (see docstring).
    memo: dict[tuple[int, ...], tuple[int, ...]] = {}

    for gi, group in enumerate(groups):
        pitches = [pitch for pitch, _fret in group]
        key = tuple(pitches)
        cached = memo.get(key)
        if cached is not None:
            group_lanes = list(cached)
        else:
            if gi == 0:
                center_lane = CENTER_LANE
            else:
                delta = pitches[0] - prev_group_first_pitch
                lane_delta = round(delta / SEMITONES_PER_LANE)
                if lane_delta == 0:
                    lane_delta = 1 if delta > 0 else (-1 if delta < 0 else 0)
                center_lane = max(0, min(4, anchor_lane + lane_delta))
            group_lanes = _rank_order_lanes(pitches, center_lane)

            # Boundary nudge: if this group's first note would land on the
            # exact same lane as the previous group's last note despite a
            # different pitch, shift the WHOLE group by one lane (keeps
            # its internal rank spacing intact) - computed once here, then
            # baked into the memo, never recomputed on a later replay.
            if (
                prev_group_last_pitch is not None
                and group_lanes[0] == prev_group_last_lane
                and pitches[0] != prev_group_last_pitch
            ):
                direction = 1 if pitches[0] > prev_group_last_pitch else -1
                if all(0 <= lane + direction <= 4 for lane in group_lanes):
                    group_lanes = [lane + direction for lane in group_lanes]
                elif all(0 <= lane - direction <= 4 for lane in group_lanes):
                    group_lanes = [lane - direction for lane in group_lanes]
            memo[key] = tuple(group_lanes)

        lanes.extend(group_lanes)
        anchor_lane = CENTER_LANE
        prev_group_first_pitch = pitches[0]
        prev_group_last_pitch = pitches[-1]
        prev_group_last_lane = group_lanes[-1]
    return lanes


def map_notes(ir_notes: list[dict[str, Any]]) -> list[ChartNote]:
    """Map a single track's (or blended) IR note list to chart notes."""
    notes = _merge_ties(ir_notes)
    chug_string = _lowest_tuning_string(notes)

    # Group simultaneous notes (chords share a tick; chord_id guards
    # against two tracks' blended notes colliding on one tick).
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for note in notes:
        groups.setdefault((note["tick"], note.get("chord_id")), []).append(note)
    ordered_groups = sorted(groups.items(), key=lambda kv: kv[0][0])

    # Open-string chugs bypass lane logic entirely and don't participate
    # in hand-position grouping; regular single notes are pulled out into
    # their own pitch sequence so _single_note_lanes sees a continuous,
    # uninterrupted stream to group/rank/memoize (an open note or a chord
    # in between two single notes shouldn't count as a "leap"). Chords get
    # the same continuous-stream treatment via _chord_lanes_sequence.
    single_notes: list[tuple[int, int]] = []  # (pitch, fret)
    single_note_positions: list[int] = []  # index into ordered_groups
    chord_shapes: list[tuple[int, ...]] = []
    chord_positions: list[int] = []
    for gi, ((tick, _), group) in enumerate(ordered_groups):
        if len(group) == 1:
            if not (group[0]["fret"] == 0 and group[0]["string"] == chug_string):
                single_notes.append((group[0]["pitch"] or 0, group[0]["fret"] or 0))
                single_note_positions.append(gi)
        else:
            chord_shapes.append(tuple(sorted(n["pitch"] or 0 for n in group)))
            chord_positions.append(gi)
    single_lanes = _single_note_lanes(single_notes)
    lanes_by_position = dict(zip(single_note_positions, single_lanes))
    chord_lanes_seq = _chord_lanes_sequence(chord_shapes)
    lanes_by_position.update(zip(chord_positions, chord_lanes_seq))

    chart_notes: list[ChartNote] = []
    for gi, ((tick, _), group) in enumerate(ordered_groups):
        if len(group) == 1 and group[0]["fret"] == 0 and group[0]["string"] == chug_string:
            lanes = [OPEN_NOTE]
        elif len(group) == 1:
            lanes = [lanes_by_position[gi]]
        else:
            lanes = lanes_by_position[gi]
        duration = max(n["duration_ticks"] for n in group)
        source = {"ir_tick": tick}
        if len(group) == 1:
            source["pitch"] = group[0]["pitch"]
        chart_notes.append(
            ChartNote(
                tick=_to_chart_ticks(tick),
                lanes=sorted(set(lanes)),
                sustain=_to_chart_ticks(duration),
                forced=any(n.get("hammer_on") or n.get("pull_off") for n in group),
                tap=any(n.get("tap") for n in group),
                source=source,
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
