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

Chord voicing uses the SAME anchor/leap/rank-order/memoize design as
single notes (see `_chord_lanes`): a chord's root pitch anchors it the
same way a single note's pitch does, the next chord's target lane comes
from a proportional leap off the previous chord's root, and an exact-
repeat chord shape is memoized and replayed verbatim. Within one chord,
its own distinct pitches are laid out around the target lane with a gap
whenever two adjacent pitches are more than an octave apart (a
"disjoint" voicing, e.g. a two-hand tapped octave) - adjacent lanes
should mean "these pitches are close together," capped at 3 lanes wide
(game plan rule 3's cap). This replaced an earlier absolute-pitch
formula (`root % N`) after a real chord progression showed it jumping
the full lane range for a 3-semitone chord shift (confirmed against
"Still Searching" track 1: (59,71)->(56,68) jumped [3,4]->[0,1] despite
only a minor-3rd root movement) - the same class of bug the old
absolute pitch-mod-5 single-note mapping had.

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

# Chord pitches (sorted) more than an octave apart get a lane gap instead
# of stacking contiguous - adjacent lanes should mean "close together."
DISJOINT_CHORD_SEMITONES = 12

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


def _chord_rank_lanes(pitches: tuple[int, ...], center_lane: int) -> dict[int, int]:
    """Lay a chord's DISTINCT pitches (sorted) around center_lane, one
    step per adjacent pair, gapped by 2 lanes instead of 1 when that pair
    is a disjoint voicing (> 1 octave apart). Capped to the lowest 3
    distinct pitches (game plan rule 3's width cap) - any pitch beyond
    that has no entry in the returned map."""
    distinct = sorted(set(pitches))[:3]
    if len(distinct) == 1:
        return {distinct[0]: center_lane}
    steps = [
        2 if (distinct[i] - distinct[i - 1]) > DISJOINT_CHORD_SEMITONES else 1
        for i in range(1, len(distinct))
    ]
    span = min(sum(steps), 4)
    lo_lane = max(0, min(4 - span, center_lane - span // 2))
    lanes = [lo_lane]
    lane = lo_lane
    for step in steps:
        lane = min(4, lane + step)
        lanes.append(lane)
    return dict(zip(distinct, lanes))


def _chord_lanes_sequence(chords: list[tuple[int, ...]]) -> list[list[int]]:
    """Lane for each chord in a track, in order - same anchor/leap/
    memoize design as _single_note_lanes, keyed on the chord's root
    pitch (its lowest note) instead of a single-note pitch. See the
    module docstring for the full rationale."""
    lanes_seq: list[list[int]] = []
    anchor_lane = CENTER_LANE
    prev_root: int | None = None
    memo: dict[tuple[int, ...], list[int]] = {}

    for chord in chords:
        cached = memo.get(chord)
        if cached is not None:
            lanes_seq.append(cached)
            anchor_lane = CENTER_LANE
            prev_root = chord[0]
            continue

        root = chord[0]
        if prev_root is None:
            center_lane = CENTER_LANE
        else:
            delta = root - prev_root
            lane_delta = round(delta / SEMITONES_PER_LANE)
            if lane_delta == 0:
                lane_delta = 1 if delta > 0 else (-1 if delta < 0 else 0)
            center_lane = max(0, min(4, anchor_lane + lane_delta))
        rank_map = _chord_rank_lanes(chord, center_lane)
        chord_lanes = [rank_map[p] for p in chord if p in rank_map]
        memo[chord] = chord_lanes
        lanes_seq.append(chord_lanes)
        anchor_lane = CENTER_LANE
        prev_root = root
    return lanes_seq


def _group_into_hand_positions(pitches: list[int]) -> list[list[int]]:
    """Split a sequence of single-note pitches into runs where every note
    stays within HAND_POSITION_SEMITONES of the run's first pitch."""
    groups: list[list[int]] = []
    current: list[int] = []
    anchor: int | None = None
    for pitch in pitches:
        if anchor is None or abs(pitch - anchor) > HAND_POSITION_SEMITONES:
            if current:
                groups.append(current)
            current = [pitch]
            anchor = pitch
        else:
            current.append(pitch)
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


def _single_note_lanes(pitches: list[int]) -> list[int]:
    """Lane for each single note in a track, in order. See the module
    docstring for the full rank-order + leap + memoize design."""
    groups = _group_into_hand_positions(pitches)
    lanes: list[int] = []
    anchor_lane = CENTER_LANE
    prev_group_first_pitch: int | None = None
    prev_group_last_pitch: int | None = None
    prev_group_last_lane: int | None = None
    # Exact-repeat memo: same pitch sequence -> same final lane sequence,
    # regardless of what happens to precede it this time (see docstring).
    memo: dict[tuple[int, ...], tuple[int, ...]] = {}

    for gi, group in enumerate(groups):
        key = tuple(group)
        cached = memo.get(key)
        if cached is not None:
            group_lanes = list(cached)
        else:
            if gi == 0:
                center_lane = CENTER_LANE
            else:
                delta = group[0] - prev_group_first_pitch
                lane_delta = round(delta / SEMITONES_PER_LANE)
                if lane_delta == 0:
                    lane_delta = 1 if delta > 0 else (-1 if delta < 0 else 0)
                center_lane = max(0, min(4, anchor_lane + lane_delta))
            group_lanes = _rank_order_lanes(group, center_lane)

            # Boundary nudge: if this group's first note would land on the
            # exact same lane as the previous group's last note despite a
            # different pitch, shift the WHOLE group by one lane (keeps
            # its internal rank spacing intact) - computed once here, then
            # baked into the memo, never recomputed on a later replay.
            if (
                prev_group_last_pitch is not None
                and group_lanes[0] == prev_group_last_lane
                and group[0] != prev_group_last_pitch
            ):
                direction = 1 if group[0] > prev_group_last_pitch else -1
                if all(0 <= lane + direction <= 4 for lane in group_lanes):
                    group_lanes = [lane + direction for lane in group_lanes]
                elif all(0 <= lane - direction <= 4 for lane in group_lanes):
                    group_lanes = [lane - direction for lane in group_lanes]
            memo[key] = tuple(group_lanes)

        lanes.extend(group_lanes)
        anchor_lane = CENTER_LANE
        prev_group_first_pitch = group[0]
        prev_group_last_pitch = group[-1]
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
    single_note_pitches: list[int] = []
    single_note_positions: list[int] = []  # index into ordered_groups
    chord_shapes: list[tuple[int, ...]] = []
    chord_positions: list[int] = []
    for gi, ((tick, _), group) in enumerate(ordered_groups):
        if len(group) == 1:
            if not (group[0]["fret"] == 0 and group[0]["string"] == chug_string):
                single_note_pitches.append(group[0]["pitch"] or 0)
                single_note_positions.append(gi)
        else:
            chord_shapes.append(tuple(sorted(n["pitch"] or 0 for n in group)))
            chord_positions.append(gi)
    single_lanes = _single_note_lanes(single_note_pitches)
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
