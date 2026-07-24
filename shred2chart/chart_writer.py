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

from dataclasses import replace
from pathlib import Path
from typing import Any

from .mapper import (
    FORCED_FLAG,
    TAP_FLAG,
    CHART_RESOLUTION,
    IR_TICKS_PER_QUARTER,
    ChartNote,
    _to_chart_ticks,
)
from .validation import escape_metadata

DEFAULT_LEAD_IN_BARS = 2


def add_lead_in(
    tempo_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chart_notes: list[ChartNote],
    bars: int = DEFAULT_LEAD_IN_BARS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ChartNote], int]:
    """Insert whole empty measures before the score timeline.

    Notes and sections move together. The original tempo map also moves, but
    copies of the starting tempo and time signature remain at tick 0 so the
    pre-roll itself uses the song's real measure length instead of Clone
    Hero's implicit 120 BPM / 4/4 defaults.

    GP events use 960 ticks/quarter while chart notes already use 192. The
    returned lead-in duration is milliseconds at the starting tempo; callers
    use it to delay unmodified audio by the same musical duration.
    """
    if bars <= 0:
        return tempo_events, sections, chart_notes, 0

    starting: dict[str, dict[str, Any]] = {}
    for event in sorted(tempo_events, key=lambda event: event["tick"]):
        if event["tick"] > 0:
            break
        if event["type"] in {"tempo", "time_signature"}:
            starting[event["type"]] = event

    time_signature = starting.get(
        "time_signature",
        {"numerator": 4, "denominator": 4},
    )
    numerator = time_signature["numerator"]
    denominator = time_signature["denominator"]
    bar_quarters = numerator * 4 / denominator

    starting_tempo = starting.get("tempo", {"bpm": 120.0})
    bpm = float(starting_tempo["bpm"])
    ir_shift = round(bar_quarters * IR_TICKS_PER_QUARTER * bars)
    chart_shift = round(bar_quarters * CHART_RESOLUTION * bars)
    lead_in_ms = round(bar_quarters * bars * 60_000 / bpm)

    seed_events = [{**event, "tick": 0} for event in starting.values()]
    shifted_tempo = seed_events + [
        {**event, "tick": event["tick"] + ir_shift}
        for event in tempo_events
    ]
    shifted_sections = [
        {**section, "tick": section["tick"] + ir_shift}
        for section in sections
    ]
    shifted_notes = [
        replace(note, tick=note.tick + chart_shift)
        for note in chart_notes
    ]
    return shifted_tempo, shifted_sections, shifted_notes, lead_in_ms


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
    song_length_ms = compute_song_length_ms(chart_notes, tempo_events)
    (out_path / "notes.chart").write_text(chart_text, encoding="utf-8")
    (out_path / "song.ini").write_text(
        build_song_ini(title, artist, offset_ms, charter, song_length_ms),
        encoding="utf-8",
    )
    return out_path
