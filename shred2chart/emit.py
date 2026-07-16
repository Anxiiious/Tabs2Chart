"""Stage 5 — Emit: write .chart and song.ini for Clone Hero.

.chart format (text):
  [Song]        — metadata
  [SyncTrack]   — B (tempo) and TS (time-signature) events
  [Events]      — E "section <name>" rehearsal markers
  [ExpertSingle]— N <lane> <duration> note events, plus N 5 / N 6 flag lines

Note-flag semantics verified against the community .chart spec
(see <https://github.com/FireFox2000000/Moonscraper-Chart-Editor/blob/master/
      Docs/Moonscraper%20Chart%20Editor%20Docs.md> and the GH community wiki):

  N 0–4 : fret lane Green → Orange
  N 5   : force HOPO (hammer-on / pull-off) flag on same tick
  N 6   : force Tap flag on same tick (implies HOPO; takes priority over N 5)
  N 7   : open note

song.ini keys used by Clone Hero:
  name, artist, album, charter, delay (ms), diff_guitar, song_length
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import List

from .config import Config
from .ir import IRSong
from .mapping import ChartNote
from .synctrack import SyncEvent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_song_folder(
    out_dir: str | Path,
    ir: IRSong,
    sync_events: List[SyncEvent],
    chart_notes: List[ChartNote],
    config: Config,
) -> None:
    """Write ``notes.chart`` and ``song.ini`` into *out_dir*.

    *out_dir* is created if it does not exist.  Existing files are
    overwritten silently (idempotent re-runs).

    Parameters
    ----------
    out_dir:
        Destination folder (the Clone Hero song folder).
    ir:
        Populated IR; supplies title, artist, sections, resolution.
    sync_events:
        Output of :func:`~shred2chart.synctrack.build_synctrack`.
    chart_notes:
        Output of :func:`~shred2chart.mapping.map_notes`.
    config:
        Config with offset_ms and charter fields.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_path = out_dir / "notes.chart"
    ini_path = out_dir / "song.ini"

    with chart_path.open("w", encoding="utf-8") as fh:
        fh.write(build_chart(ir, sync_events, chart_notes, config))

    with ini_path.open("w", encoding="utf-8") as fh:
        fh.write(build_song_ini(ir, config))

    print(f"✓  Chart written to  {chart_path}")
    print(f"✓  song.ini written to  {ini_path}")
    print(
        "   Drop your audio file (song.ogg or song.mp3) into:\n"
        f"   {out_dir}"
    )


def build_chart(
    ir: IRSong,
    sync_events: List[SyncEvent],
    chart_notes: List[ChartNote],
    config: Config,
) -> str:
    """Return the full text content of a ``notes.chart`` file."""
    parts: List[str] = []

    parts.append(_section_song(ir, config))
    parts.append(_section_synctrack(sync_events))
    parts.append(_section_events(ir))
    parts.append(_section_expert_single(chart_notes))

    return "\n".join(parts) + "\n"


def build_song_ini(ir: IRSong, config: Config) -> str:
    """Return the text content of a ``song.ini`` file."""
    lines = [
        "[song]",
        f"name = {ir.title or 'Unknown Title'}",
        f"artist = {ir.artist or 'Unknown Artist'}",
        f"album = {ir.album or ''}",
        f"charter = {config.charter}",
        f"delay = {config.offset_ms}",
        "diff_guitar = -1",
        "song_length = 0",
        "icon = ",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _section_song(ir: IRSong, config: Config) -> str:
    """Build the [Song] header block."""
    offset_secs = config.offset_ms / 1000.0
    lines = [
        "[Song]",
        "{",
        f'  Name = "{ir.title or "Unknown Title"}"',
        f'  Artist = "{ir.artist or "Unknown Artist"}"',
        f'  Charter = "{config.charter}"',
        f"  Offset = {offset_secs:.3f}",
        f"  Resolution = {ir.resolution}",
        "  Player2 = bass",
        "  Difficulty = 0",
        "  PreviewStart = 0",
        "  PreviewEnd = 0",
        '  Genre = "rock"',
        '  MediaType = "cd"',
        '  MusicStream = "song.ogg"',
        "}",
    ]
    return "\n".join(lines)


def _section_synctrack(sync_events: List[SyncEvent]) -> str:
    """Build the [SyncTrack] block."""
    lines = ["[SyncTrack]", "{"]
    for ev in sync_events:
        if ev.kind == "B":
            lines.append(f"  {ev.tick} = B {ev.values[0]}")
        elif ev.kind == "TS":
            num, denom_exp = ev.values
            # Omit denom_exp when it equals 2 (conventional /4 default).
            if denom_exp == 2:
                lines.append(f"  {ev.tick} = TS {num}")
            else:
                lines.append(f"  {ev.tick} = TS {num} {denom_exp}")
    lines.append("}")
    return "\n".join(lines)


def _section_events(ir: IRSong) -> str:
    """Build the [Events] block with section markers."""
    lines = ["[Events]", "{"]
    for sec in sorted(ir.sections, key=lambda s: s.tick):
        # Sanitise section name: strip quotes.
        safe_name = sec.name.replace('"', "'")
        lines.append(f'  {sec.tick} = E "section {safe_name}"')
    lines.append("}")
    return "\n".join(lines)


def _section_expert_single(chart_notes: List[ChartNote]) -> str:
    """Build the [ExpertSingle] block."""
    lines = ["[ExpertSingle]", "{"]

    # Sort by tick; within same tick: note lines before flag lines.
    notes_sorted = sorted(chart_notes, key=lambda n: n.tick)

    for cn in notes_sorted:
        lines.append(f"  {cn.tick} = N {cn.lane} {cn.duration_ticks}")
        if cn.tap:
            lines.append(f"  {cn.tick} = N 6 0")
        elif cn.hopo:
            lines.append(f"  {cn.tick} = N 5 0")

    lines.append("}")
    return "\n".join(lines)
