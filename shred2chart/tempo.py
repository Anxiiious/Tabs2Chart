"""Tempo/time-signature map extraction from parsed Guitar Pro files.

Works on .gp3/.gp4/.gp5 (whatever PyGuitarPro can parse) — the output of
converting a .gpx through Guitar Pro or TuxGuitar (Route A). This is the
"post-conversion" side of the M0 verification described in
SHRED2CHART_GAMEPLAN.md: dump these events and compare them against the
tempo markers visible in the original .gpx (opened in the same app, or
the extracted score.gpif via `shred2chart dump-gpif`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import guitarpro

TICKS_PER_QUARTER = guitarpro.Duration.quarterTime


def dump_tempo_events(path: str | Path) -> list[dict[str, Any]]:
    """Return a tick-ordered list of tempo and time-signature events.

    Each event is one of:
      {"tick": int, "type": "tempo", "bpm": int}
      {"tick": int, "type": "time_signature", "numerator": int, "denominator": int}
    """
    song = guitarpro.parse(str(path))
    events: list[dict[str, Any]] = [
        {"tick": 0, "type": "tempo", "bpm": song.tempo},
    ]

    track = song.tracks[0]
    last_ts: tuple[int, int] | None = None
    for measure in track.measures:
        ts = measure.timeSignature
        ts_key = (ts.numerator, ts.denominator.value)
        if ts_key != last_ts:
            events.append({
                "tick": measure.start,
                "type": "time_signature",
                "numerator": ts.numerator,
                "denominator": ts.denominator.value,
            })
            last_ts = ts_key

        for voice in measure.voices:
            for beat in voice.beats:
                mtc = beat.effect.mixTableChange
                if mtc is not None and mtc.tempo is not None:
                    events.append({"tick": beat.start, "type": "tempo", "bpm": mtc.tempo.value})

    events.sort(key=lambda e: e["tick"])
    return events
