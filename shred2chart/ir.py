"""Intermediate representation (IR) for shred2chart.

All musical events are normalised to 192-tick-per-quarter-note positions
before any mapping or emit logic runs.  This decouples the ingest stage from
every downstream stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class NoteEvent:
    """A single fretted/open note from the source tab."""

    tick: int
    """Absolute position in chart ticks (192 ticks/quarter note = 0-indexed)."""

    duration_ticks: int
    """Sounding length in chart ticks; 0 for untied short notes."""

    pitch: int
    """MIDI pitch number derived from string tuning + fret."""

    string: int
    """Guitar string number, 1 = highest (thinnest) pitched string."""

    fret: int
    """Fret number; 0 = open string."""

    chord_id: Optional[int] = None
    """Notes sharing the same tick in a chord share the same chord_id."""

    # ── Technique flags ───────────────────────────────────────────────────────
    hammer_on: bool = False
    pull_off: bool = False
    tap: bool = False
    slide_out: bool = False
    slide_in: bool = False
    palm_mute: bool = False
    dead_note: bool = False
    bend: bool = False
    vibrato: bool = False
    tremolo_picked: bool = False


@dataclass
class TempoEvent:
    """A tempo change at a given tick position."""

    tick: int
    bpm: float
    """Beats per minute at this tick."""

    linear_ramp_to: Optional[float] = None
    """If set, the tempo glides linearly from *bpm* to this value by the next
    TempoEvent.  Present only when parsed from .gpx XML automations; always
    None for .gp5 files (which only have step-wise changes)."""


@dataclass
class TimeSignatureEvent:
    """A time-signature change at a given tick position."""

    tick: int
    numerator: int
    denominator: int
    """Denominator as a plain integer (4 = quarter note, 8 = eighth note, …)."""


@dataclass
class SectionEvent:
    """A named section marker (from Guitar Pro rehearsal markers)."""

    tick: int
    name: str


@dataclass
class IRSong:
    """Top-level container holding all IR events for a single song."""

    title: str = ""
    artist: str = ""
    album: str = ""
    resolution: int = 192
    """Ticks per quarter note — always 192 inside this tool."""

    string_count: int = 6
    tuning: List[int] = field(
        default_factory=lambda: [64, 59, 55, 50, 45, 40]
    )
    """MIDI pitch for each open string, index 0 = highest (thinnest) string.
    Default: standard 6-string (E4 B3 G3 D3 A2 E2)."""

    notes: List[NoteEvent] = field(default_factory=list)
    tempo_events: List[TempoEvent] = field(default_factory=list)
    time_signatures: List[TimeSignatureEvent] = field(default_factory=list)
    sections: List[SectionEvent] = field(default_factory=list)
