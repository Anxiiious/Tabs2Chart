"""IR notes -> Clone Hero 5-lane note events (Stage 4, M4 contour mapping).

- **Single notes use a static-anchor contour with proportional lane steps**
  (see `_LaneContour`/`_assign_lanes`): lanes move *relative* to a
  hand-position anchor rather than from absolute pitch, so a melody reads
  as a smooth up/down contour instead of jumping around whenever
  pitch-mod-5 happens to land far away. The anchor stays fixed while the
  melody moves within one hand position (a real guitarist doesn't shift
  position for every note), re-centering only on a genuine
  position-shift-sized leap (`HAND_POSITION_SEMITONES`). Lane movement is
  proportional to how far the pitch actually moved
  (`SEMITONES_PER_LANE`) rather than a flat +-1 step — a v1 version used
  a flat step and wide, repeat-alternating leaps got stuck oscillating in
  2 lanes at whichever edge the anchor first clamped against (confirmed
  against a real lead lick; see SHRED2CHART_GAMEPLAN.md's 2026-07-19
  entry). The static anchor also gives pattern stability for the common
  case: the same riff played twice in the same hand position maps to the
  identical lane sequence both times.
- **Ties merge into sustains** (the EOF-confirmed behavior): a note with
  `tied: True` extends the previous note at the same string+pitch
  instead of becoming a new attack.
- **Open-string chugs -> open note (N 7)**: fret 0 on the track's
  lowest-tuned string. The tuning is inferred from the notes themselves
  (pitch - fret = the string's tuning), so drop tunings work without
  any tuning metadata.
- **Technique flags**: hammer_on/pull_off -> forced flip (`N 5`),
  tap -> tap modifier (`N 6`, which overrides HOPO per the spec).

Chord voicing: root lane from the root pitch, remaining chord notes
stacked on adjacent lanes, capped at 3 lanes wide (game plan rule 3's
cap) — except a "disjoint" voicing (widely separated pitches, > 1 octave
apart, e.g. a two-hand tapped octave) leaves a gap between lanes instead
of stacking them contiguous, per the same rationale as the single-note
contour: adjacent lanes should mean "these pitches are close together."

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

# A note within this many semitones of the anchor is considered "the same
# hand position" (roughly a real guitarist's 4-5 fret span) - crossing it
# re-centers the anchor on the new pitch (see _assign_lanes).
HAND_POSITION_SEMITONES = 4

# How many semitones of pitch movement equal one lane step. Proportional
# (not flat +-1) so a wide leap visibly spreads across more of the neck
# than a small one, instead of both moving identically - a flat step let
# repeat-alternating wide leaps get stuck oscillating in 2 lanes at
# whichever edge the anchor first clamped against (confirmed against a
# real 40-note lead lick, tick 92000-110880 of "Still Searching" track 1,
# during M4 v2 - see SHRED2CHART_GAMEPLAN.md's 2026-07-19 entry).
SEMITONES_PER_LANE = 3

# Chord pitches (sorted) more than an octave apart get a lane gap instead
# of stacking contiguous - adjacent lanes should mean "close together."
DISJOINT_CHORD_SEMITONES = 12


class _LaneContour:
    """Tracks the single-note lane-contour state across a `map_notes` run.

    `anchor_pitch`/`anchor_lane` model where a guitarist's hand currently
    sits on the neck. Both start `None` and are set by the first fretted
    note encountered.
    """

    def __init__(self) -> None:
        self.anchor_pitch: int | None = None
        self.anchor_lane: int | None = None


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


def _assign_lanes(
    group: list[dict[str, Any]], chug_string: int | None, contour: _LaneContour
) -> list[int]:
    """M4 lane assignment for one beat's notes (single or chord)."""
    if len(group) == 1:
        note = group[0]
        if note["fret"] == 0 and note["string"] == chug_string:
            return [OPEN_NOTE]

        pitch = note["pitch"] or 0
        if contour.anchor_pitch is None:
            contour.anchor_pitch = pitch
            contour.anchor_lane = pitch % 5
            return [contour.anchor_lane]

        delta = pitch - contour.anchor_pitch
        if delta == 0:
            return [contour.anchor_lane]

        # Lane movement is proportional to how far the pitch actually
        # moved (not a flat +-1), so a wide leap spreads further across
        # the neck than a small one - see SEMITONES_PER_LANE above.
        lane_delta = round(delta / SEMITONES_PER_LANE)
        if lane_delta == 0:
            lane_delta = 1 if delta > 0 else -1  # any nonzero delta moves >=1 lane
        new_lane = max(0, min(4, contour.anchor_lane + lane_delta))

        if abs(delta) > HAND_POSITION_SEMITONES:
            # Position shift: this note becomes the new anchor, at the
            # lane just computed - preserves both direction and magnitude
            # rather than snapping to an absolute-pitch lane.
            contour.anchor_pitch = pitch
            contour.anchor_lane = new_lane

        return [new_lane]

    pitches = sorted({n["pitch"] or 0 for n in group})
    width = min(len(pitches), 3)
    base = pitches[0] % (5 - (width - 1))  # keep the whole stack on the neck

    lanes = [base]
    lane = base
    for i in range(1, width):
        gap = pitches[i] - pitches[i - 1]
        step = 2 if gap > DISJOINT_CHORD_SEMITONES else 1
        lane = min(4, lane + step)
        lanes.append(lane)
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

    contour = _LaneContour()
    chart_notes: list[ChartNote] = []
    for (tick, _), group in sorted(groups.items(), key=lambda kv: kv[0][0]):
        lanes = _assign_lanes(group, chug_string, contour)
        duration = max(n["duration_ticks"] for n in group)
        chart_notes.append(
            ChartNote(
                tick=_to_chart_ticks(tick),
                lanes=sorted(set(lanes)),
                sustain=_to_chart_ticks(duration),
                forced=any(n.get("hammer_on") or n.get("pull_off") for n in group),
                tap=any(n.get("tap") for n in group),
                source={"ir_tick": tick},
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
