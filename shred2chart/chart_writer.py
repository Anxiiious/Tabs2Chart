"""Emit a Clone Hero song folder: notes.chart + song.ini (Stage 5).

Format details pinned from the community .chart docs (TheNathannator's
GuitarGame_ChartFormats — Format-Overview and 5-Fret-Guitar pages), per
the game plan's "do not code note flags from memory" mandate:

- [Song]: Resolution = ticks per quarter (we emit 192, the standard);
  Offset is in *seconds* (decimal); string values are quoted.
- [SyncTrack]: `tick = B <bpm*1000>` (last 3 digits are decimals);
  `tick = TS <numerator> [<log2 denominator>]`, exponent omitted for /4.
- [Events]: `tick = E "section <name>"`.
- [ExpertSingle]: `tick = N <type> <length>`; 0-4 = green..orange,
  5 = strum/HOPO flip modifier, 6 = tap modifier, 7 = open. Modifier
  lines sit at the same tick as the note they modify.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .mapper import FORCED_FLAG, TAP_FLAG, CHART_RESOLUTION, ChartNote, _to_chart_ticks
from .validation import escape_metadata


def _sync_track_lines(tempo_events: list[dict[str, Any]]) -> list[str]:
    lines = []
    for event in sorted(tempo_events, key=lambda e: (e["tick"], e["type"] != "time_signature")):
        tick = _to_chart_ticks(event["tick"])
        if event["type"] == "tempo":
            lines.append(f"  {tick} = B {round(event['bpm'] * 1000)}")
        elif event["type"] == "time_signature":
            denominator = event["denominator"]
            if denominator == 4:
                lines.append(f"  {tick} = TS {event['numerator']}")
            else:
                if denominator <= 0 or (denominator & (denominator - 1)) != 0:
                    raise ValueError(
                        f"time signature denominator must be a positive power of two, "
                        f"got {denominator} (tick {event['tick']})"
                    )
                exponent = denominator.bit_length() - 1
                lines.append(f"  {tick} = TS {event['numerator']} {exponent}")
    return lines


def _events_lines(sections: list[dict[str, Any]]) -> list[str]:
    return [
        f'  {_to_chart_ticks(s["tick"])} = E "section {s["name"]}"'
        for s in sorted(sections, key=lambda s: s["tick"])
    ]


def _note_lines(chart_notes: list[ChartNote]) -> list[str]:
    lines = []
    for note in chart_notes:
        for lane in note.lanes:
            lines.append(f"  {note.tick} = N {lane} {note.sustain}")
        if note.tap:
            lines.append(f"  {note.tick} = N {TAP_FLAG} 0")
        elif note.forced:
            lines.append(f"  {note.tick} = N {FORCED_FLAG} 0")
    return lines


def compute_song_length_ms(
    chart_notes: list[ChartNote],
    tempo_events: list[dict[str, Any]],
) -> int:
    """Estimate song length in milliseconds from the last chart note.

    This is a chart-duration estimate, not a measurement of the actual
    audio file — callers that need the real playback length should prefer
    the audio file's own duration when one is available.

    Converts the last note's chart tick back to wall-clock time using the
    tempo map.  Returns 0 if there are no notes or no tempo information.
    """
    if not chart_notes or not tempo_events:
        return 0

    last_tick = max(n.tick + n.sustain for n in chart_notes)

    # Rebuild a simple tick->ms map from the tempo events (chart resolution).
    tempos = sorted(
        (e for e in tempo_events if e["type"] == "tempo"),
        key=lambda e: e["tick"],
    )
    if not tempos:
        return 0

    # Convert IR ticks to chart ticks for the tempo event positions.
    from .mapper import _to_chart_ticks as _tc  # noqa: PLC0415 (local import ok here)

    ms = 0.0
    # Tempo events are usually preceded by one at tick 0, but that's an
    # invariant of the source file, not something this function enforces —
    # if the first event starts later, the tempo it declares is assumed to
    # apply retroactively back to tick 0 rather than silently dropping that
    # span from the total.
    first_start_tick = _tc(tempos[0]["tick"])
    if first_start_tick > 0:
        ms_per_tick = 60_000.0 / (tempos[0]["bpm"] * CHART_RESOLUTION)
        ms += min(first_start_tick, last_tick) * ms_per_tick

    for i, ev in enumerate(tempos):
        start_tick = _tc(ev["tick"])
        end_tick = _tc(tempos[i + 1]["tick"]) if i + 1 < len(tempos) else last_tick
        if start_tick >= last_tick:
            break
        span = min(end_tick, last_tick) - start_tick
        ms_per_tick = 60_000.0 / (ev["bpm"] * CHART_RESOLUTION)
        ms += span * ms_per_tick

    return round(ms)


def build_chart(
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chart_notes: list[ChartNote],
    offset_ms: int = 0,
    charter: str = "shred2chart",
) -> str:
    def block(name: str, lines: list[str]) -> str:
        body = "\n".join(lines)
        return f"[{name}]\n{{\n{body}\n}}\n"

    safe_title = escape_metadata(title)
    safe_artist = escape_metadata(artist)
    safe_charter = escape_metadata(charter)
    song_lines = [
        f'  Name = "{safe_title}"',
        f'  Artist = "{safe_artist}"',
        f'  Charter = "{safe_charter}"',
        # Same offset_ms value also becomes song.ini's `delay` (in ms) below.
        # Whether Clone Hero applies both, prefers one, or double-applies the
        # delay if both are present is an open question — see the game
        # plan's Open Questions for the sign/unit verification this needs.
        f"  Offset = {offset_ms / 1000}",
        f"  Resolution = {CHART_RESOLUTION}",
        '  MusicStream = "song.ogg"',
    ]
    parts = [
        block("Song", song_lines),
        block("SyncTrack", _sync_track_lines(tempo_events)),
        block("Events", _events_lines(sections)),
        block("ExpertSingle", _note_lines(chart_notes)),
    ]
    return "\n".join(parts)


def build_song_ini(
    title: str,
    artist: str,
    offset_ms: int = 0,
    charter: str = "shred2chart",
    song_length_ms: int = 0,
) -> str:
    safe_title = escape_metadata(title)
    safe_artist = escape_metadata(artist)
    safe_charter = escape_metadata(charter)
    lines = [
        "[song]",
        f"name = {safe_title}",
        f"artist = {safe_artist}",
        f"charter = {safe_charter}",
        # Same offset_ms value as notes.chart's `Offset` (in seconds) above —
        # two independently-interpreted sync controls from one source value.
        f"delay = {offset_ms}",
        "diff_guitar = -1",
    ]
    if song_length_ms > 0:
        lines.append(f"song_length = {song_length_ms}")
    return "\n".join(lines) + "\n"


def write_song_folder(
    out_dir: str | Path,
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chart_notes: list[ChartNote],
    offset_ms: int = 0,
    charter: str = "shred2chart",
) -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    chart_text = build_chart(title, artist, tempo_events, sections, chart_notes, offset_ms, charter)
    # Offset shifts every chart tick's real playback time by offset_ms, so
    # the last note's real-world position — and therefore the reported
    # song length — moves with it too.
    song_length_ms = compute_song_length_ms(chart_notes, tempo_events) + offset_ms
    if song_length_ms < 0:
        song_length_ms = 0
    (out_path / "notes.chart").write_text(chart_text, encoding="utf-8")
    (out_path / "song.ini").write_text(
        build_song_ini(title, artist, offset_ms, charter, song_length_ms),
        encoding="utf-8",
    )
    return out_path
