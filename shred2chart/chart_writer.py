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


def build_chart(
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chart_notes: list[ChartNote],
    offset_ms: int = 0,
) -> str:
    def block(name: str, lines: list[str]) -> str:
        body = "\n".join(lines)
        return f"[{name}]\n{{\n{body}\n}}\n"

    song_lines = [
        f'  Name = "{title}"',
        f'  Artist = "{artist}"',
        '  Charter = "shred2chart"',
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


def build_song_ini(title: str, artist: str, offset_ms: int = 0) -> str:
    return (
        "[song]\n"
        f"name = {title}\n"
        f"artist = {artist}\n"
        "charter = shred2chart\n"
        f"delay = {offset_ms}\n"
        "diff_guitar = -1\n"
    )


def write_song_folder(
    out_dir: str | Path,
    title: str,
    artist: str,
    tempo_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chart_notes: list[ChartNote],
    offset_ms: int = 0,
) -> Path:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    chart_text = build_chart(title, artist, tempo_events, sections, chart_notes, offset_ms)
    (out_path / "notes.chart").write_text(chart_text, encoding="utf-8")
    (out_path / "song.ini").write_text(build_song_ini(title, artist, offset_ms), encoding="utf-8")
    return out_path
