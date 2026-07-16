"""Stage 4 — Note Mapping: IR notes → Clone Hero chart notes.

Design principles (from project spec):
  1. Open-string chugs → CH open note (N 7).
  2. Pitch contour, not absolute pitch — sliding-window phrase approach.
  3. Chords by interval spread.
  4. Repeated notes stay on the same lane.
  5. Techniques → CH mechanics (HOPO / tap flags).
  6. Sustain threshold: notes shorter than ~1/8 beat get zero sustain.

The mapping is phrase-based: contour context resets at rests ≥
``config.phrase_boundary_beats`` beats or at section markers.
Within a phrase all unique pitches are mapped to the [0, 4] lane range
via linear interpolation; ≤5 unique pitches get direct 1-to-1 placement
centered in the available lanes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .config import Config
from .ir import IRSong, NoteEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

@dataclass
class ChartNote:
    """A single event in the [ExpertSingle] section of a .chart file."""

    tick: int
    lane: int
    """0–4 = Green→Orange; 7 = Open."""

    duration_ticks: int
    """Sustain length; 0 means no sustain (strummed/tapped note only)."""

    hopo: bool = False
    """When True, emit a forced N 5 flag on the same tick."""

    tap: bool = False
    """When True, emit a forced N 6 flag (tap) instead of N 5 (HOPO)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def map_notes(ir: IRSong, config: Config) -> List[ChartNote]:
    """Convert all IR notes in *ir* to a sorted list of :class:`ChartNote`.

    Parameters
    ----------
    ir:
        Fully populated :class:`~shred2chart.ir.IRSong`.
    config:
        :class:`~shred2chart.config.Config` with tuning knobs.

    Returns
    -------
    List[ChartNote]
        Sorted by tick.  Multiple notes at the same tick form a chord.
    """
    if not ir.notes:
        return []

    notes = sorted(ir.notes, key=lambda n: (n.tick, n.string))

    phrases = _identify_phrases(notes, ir, config)
    logger.debug("Identified %d phrase(s)", len(phrases))

    result: List[ChartNote] = []
    for phrase in phrases:
        result.extend(_map_phrase(phrase, ir, config))

    result.sort(key=lambda cn: cn.tick)
    return result


# ---------------------------------------------------------------------------
# Phrase segmentation
# ---------------------------------------------------------------------------

def _identify_phrases(
    notes: List[NoteEvent], ir: IRSong, config: Config
) -> List[List[NoteEvent]]:
    """Split the note list into phrases separated by rests or section markers."""
    if not notes:
        return []

    threshold_ticks = round(config.phrase_boundary_beats * ir.resolution)
    section_ticks: Set[int] = {s.tick for s in ir.sections}

    phrases: List[List[NoteEvent]] = []
    current: List[NoteEvent] = [notes[0]]

    for i in range(1, len(notes)):
        prev = notes[i - 1]
        curr = notes[i]

        # Gap between end of previous note and start of current.
        prev_end = prev.tick + prev.duration_ticks
        gap = curr.tick - prev_end

        new_phrase = gap >= threshold_ticks or curr.tick in section_ticks

        if new_phrase:
            phrases.append(current)
            current = [curr]
        else:
            current.append(curr)

    if current:
        phrases.append(current)

    return phrases


# ---------------------------------------------------------------------------
# Single-phrase mapping
# ---------------------------------------------------------------------------

def _map_phrase(
    phrase_notes: List[NoteEvent], ir: IRSong, config: Config
) -> List[ChartNote]:
    """Map one phrase of NoteEvents to ChartNotes."""
    open_strings: Set[int] = set(config.open_strings)

    # Collect all non-open pitches to build the contour map.
    pitched = [
        n for n in phrase_notes
        if not _is_open(n, open_strings)
    ]

    pitch_lane_map = _build_contour_map(pitched)

    # Group notes by tick for chord handling.
    tick_map: Dict[int, List[NoteEvent]] = {}
    for n in phrase_notes:
        tick_map.setdefault(n.tick, []).append(n)

    result: List[ChartNote] = []
    for tick in sorted(tick_map.keys()):
        tick_notes = tick_map[tick]
        opens = [n for n in tick_notes if _is_open(n, open_strings)]
        pitched_grp = [n for n in tick_notes if not _is_open(n, open_strings)]

        for n in opens:
            sustain = _calc_sustain(n.duration_ticks, ir.resolution, config)
            result.append(
                ChartNote(
                    tick=n.tick,
                    lane=7,
                    duration_ticks=sustain,
                    hopo=n.hammer_on or n.pull_off,
                    tap=n.tap,
                )
            )

        if pitched_grp:
            chord_notes = _map_chord(pitched_grp, pitch_lane_map, ir.resolution, config)
            result.extend(chord_notes)

    return result


def _is_open(note: NoteEvent, open_strings: Set[int]) -> bool:
    return note.string in open_strings and note.fret == 0


# ---------------------------------------------------------------------------
# Contour / pitch→lane mapping
# ---------------------------------------------------------------------------

def _build_contour_map(notes: List[NoteEvent]) -> Dict[int, int]:
    """Return a pitch→lane dictionary for the given set of pitched notes.

    Rules:
    - Single unique pitch → center lane (2).
    - ≤5 unique pitches  → direct mapping, centered in lanes 0–4.
    - >5 unique pitches  → linear interpolation across full 0–4 range.
    """
    if not notes:
        return {}

    unique = sorted(set(n.pitch for n in notes))
    n_unique = len(unique)

    mapping: Dict[int, int] = {}

    if n_unique == 1:
        mapping[unique[0]] = 2
        return mapping

    if n_unique <= 5:
        offset = (5 - n_unique) // 2
        for i, pitch in enumerate(unique):
            mapping[pitch] = offset + i
        return mapping

    # More than 5 distinct pitches: linear interpolation.
    lo, hi = unique[0], unique[-1]
    pitch_range = hi - lo
    for pitch in unique:
        lane = round((pitch - lo) / pitch_range * 4)
        mapping[pitch] = max(0, min(4, lane))

    return mapping


# ---------------------------------------------------------------------------
# Chord mapping
# ---------------------------------------------------------------------------

def _map_chord(
    notes: List[NoteEvent],
    pitch_lane_map: Dict[int, int],
    resolution: int,
    config: Config,
) -> List[ChartNote]:
    """Map a simultaneous group of pitched notes to ChartNotes."""
    if not notes:
        return []

    # Sort low→high pitch so the root lands on the lowest lane.
    notes_sorted = sorted(notes, key=lambda n: n.pitch)

    result: List[ChartNote] = []
    used_lanes: Set[int] = set()

    for note in notes_sorted:
        lane = pitch_lane_map.get(note.pitch, 2)

        # Resolve collision: nudge up, then down, then accept a duplicate.
        original = lane
        if lane in used_lanes:
            for delta in range(1, 5):
                if lane + delta <= 4 and (lane + delta) not in used_lanes:
                    lane = lane + delta
                    break
                if original - delta >= 0 and (original - delta) not in used_lanes:
                    lane = original - delta
                    break

        # Enforce max chord width.
        if used_lanes:
            chord_lo = min(used_lanes)
            chord_hi = max(used_lanes)
            if lane > chord_lo + config.max_chord_width - 1:
                lane = chord_lo + config.max_chord_width - 1
            if lane < chord_hi - config.max_chord_width + 1:
                lane = chord_hi - config.max_chord_width + 1
            lane = max(0, min(4, lane))

        used_lanes.add(lane)
        sustain = _calc_sustain(note.duration_ticks, resolution, config)
        result.append(
            ChartNote(
                tick=note.tick,
                lane=lane,
                duration_ticks=sustain,
                hopo=note.hammer_on or note.pull_off,
                tap=note.tap,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Sustain threshold
# ---------------------------------------------------------------------------

def _calc_sustain(duration_ticks: int, resolution: int, config: Config) -> int:
    """Return the sustain length after applying the threshold and gap trim.

    Notes shorter than ``config.sustain_threshold_beats`` beats receive 0.
    Longer notes are trimmed by a small gap (1/32 note or 10% of duration,
    whichever is larger) so adjacent notes on the same lane don't bleed into
    each other.
    """
    threshold = round(config.sustain_threshold_beats * resolution)
    if duration_ticks < threshold:
        return 0

    # Trim: leave a gap before the next hypothetical note.
    gap = max(resolution // 32, duration_ticks // 10)
    return max(0, duration_ticks - gap)
