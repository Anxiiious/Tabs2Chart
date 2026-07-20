"""IR notes -> Clone Hero 5-lane note events (Stage 4, naive M3 version).

This is deliberately the game plan's M3 "emitter skeleton" mapping —
pitch mod 5, no contour logic — plus the three cheap rules that matter
most for playability in the target repertoire:

- Ties merge into sustains (the EOF-confirmed behavior): a note with
  tied: True extends the previous note at the same string+pitch
  instead of becoming a new attack.
- Open-string chugs -> open note (N 7): fret 0 on the track's
  lowest-tuned string. The tuning is inferred from the notes themselves
  (pitch - fret = the string's tuning), so drop tunings work without
  any tuning metadata.
- Technique flags: hammer_on/pull_off -> forced flip (N 5),
  tap -> tap modifier (N 6, which overrides HOPO per the spec).

Chord voicing: root lane from the root pitch, remaining chord notes
spread by harmonic interval (see _interval_to_gap) rather than
forced onto adjacent lanes, capped at 3 lanes wide (game plan rule
3's cap).

The real contour-based mapping is M4 and replaces _assign_lanes.

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


def _assign_lanes(
    group: list[dict[str, Any]],
    chug_string: int | None,
    prev: tuple[tuple[int, ...], tuple[int, ...]] | None = None,
) -> list[int]:
    """M3.2 lane assignment for one beat's notes (single or chord).

    Chords are voiced by interval: lane gaps follow the actual
    harmonic distance between successive notes (see
    _interval_to_gap) rather than always being adjacent, so power
    chords/5ths/octaves spread across skipped lanes (e.g. 0,2 or
    1,3) instead of collapsing to 0,1.

    Anti-repeat rule: if this chord's pitches differ from the
    previous chord's but the naive anchoring would land on the exact
    same lanes (root-mod collisions, e.g. C5 and Eb5 both anchoring
    to base 0), nudge the base to the nearest free slot so the chart
    visibly moves. Identical repeated chords (chugs) keep their lanes.
    prev is (prev_pitches, prev_lanes) from the last chord.
    """
    if len(group) == 1:
        note = group[0]
        if note["fret"] == 0 and note["string"] == chug_string:
            return [OPEN_NOTE]
        return [(note["pitch"] or 0) % 5]

    pitches = sorted({n["pitch"] or 0 for n in group})
    width = min(len(pitches), 3)
    root = pitches[0]

    offsets = [0]
    for prev_p, curr_p in zip(pitches[: width - 1], pitches[1:width]):
        offsets.append(offsets[-1] + _interval_to_gap(curr_p - prev_p))

    span = offsets[-1]
    if span > 4:
        # Wider than the 5-lane neck: pin the outer notes to the full
        # width and clamp the middle inside, instead of float scaling.
        # (Unreachable with the current gap table — max span is 8 and
        # no reachable combo collides — but explicit beats clever if
        # _interval_to_gap ever changes.)
        if width == 2:
            offsets = [0, 4]
        else:
            mid = max(1, min(3, round(offsets[1] * 4 / span)))
            offsets = [0, mid, 4]
        span = 4

    n_bases = 4 - span + 1  # valid anchor positions on the neck
    base = root % n_bases
    lanes = sorted({base + o for o in offsets})

    if prev is not None:
        prev_pitches, prev_lanes = prev
        if tuple(pitches) != prev_pitches and tuple(lanes) == prev_lanes:
            if n_bases > 1:
                # Different chord, identical lanes: shift to the nearest
                # alternative anchor (prefer +1, wrap within range).
                for delta in (1, -1, 2, -2, 3, -3, 4, -4):
                    alt = base + delta
                    if 0 <= alt < n_bases:
                        lanes = sorted({alt + o for o in offsets})
                        break
            elif len(lanes) == 3:
                # Full-width 3-note chord ([0, mid, 4]): the base can't
                # move, so shift the middle note instead.
                for alt_mid in (2, 1, 3):
                    if alt_mid != lanes[1]:
                        lanes = [0, alt_mid, 4]
                        break
            else:
                # Full-width 2-note chord ([0, 4]): pull one end in a
                # lane to distinguish it (still a wide skip shape).
                lanes = [0, 3] if prev_lanes != (0, 3) else [1, 4]

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

    chart_notes: list[ChartNote] = []
    prev_chord: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    for (tick, _), group in sorted(groups.items(), key=lambda kv: kv[0][0]):
        lanes = _assign_lanes(group, chug_string, prev=prev_chord)
        if len(group) > 1 and lanes != [OPEN_NOTE]:
            prev_chord = (
                tuple(sorted({n["pitch"] or 0 for n in group})),
                tuple(lanes),
            )
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
