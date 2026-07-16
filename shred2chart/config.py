"""Configuration / tuning knobs for shred2chart.

All values are exposed via the CLI and can be overridden at run time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # ── Tuning ────────────────────────────────────────────────────────────────
    tuning: List[int] = field(
        default_factory=lambda: [64, 59, 55, 50, 45, 40]
    )
    """MIDI pitch per open string, index 0 = highest string.
    Defaults to 6-string standard (E4 B3 G3 D3 A2 E2)."""

    # ── Open-note detection ───────────────────────────────────────────────────
    open_strings: List[int] = field(default_factory=lambda: [6])
    """Guitar string numbers where fret 0 maps to Clone Hero open note (N 7).
    Default: string 6 (lowest string on a 6-string guitar).
    For 7-string add string 7 here."""

    # ── Note-mapping knobs ────────────────────────────────────────────────────
    phrase_boundary_beats: float = 1.0
    """A gap >= this many beats between notes resets the contour window."""

    max_chord_width: int = 3
    """Maximum number of lanes a chord may span (playability limit)."""

    sustain_threshold_beats: float = 0.125
    """Notes shorter than this fraction of a beat receive zero sustain."""

    # ── HOPO ──────────────────────────────────────────────────────────────────
    hopo_gap_ticks: int = 16
    """Maximum tick distance from the previous note for Clone Hero's built-in
    auto-HOPO (1/12 note ≈ 16 ticks at 192 ticks/qtr).  Used for informational
    purposes; CH calculates this itself.  Forced HOPOs come from GP flags."""

    # ── Output ────────────────────────────────────────────────────────────────
    offset_ms: int = 0
    """Global audio offset in milliseconds written to song.ini ``delay``."""

    charter: str = "shred2chart"
    """Charter name written to song.ini."""

    # ── Track selection ───────────────────────────────────────────────────────
    track_name: str = ""
    """If non-empty, select the GP track whose name matches (case-insensitive).
    Falls back to heuristics (first track containing 'lead' or 'guitar')."""
