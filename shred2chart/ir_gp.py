"""Per-note intermediate representation (IR) extraction via PyGuitarPro,
for `.gp3`/`.gp4`/`.gp5` files (see shred2chart/ir_gpif.py for the
direct-GPIF-XML equivalent, used for GP7/8 `.gp` files instead).

This is Milestone M1 from SHRED2CHART_GAMEPLAN.md: turn parsed notes into
the tick-based event list described in §4, ahead of the note-mapping
logic (Stage 4) that will eventually decide how they land on 5 CH lanes.
Only the primary voice (voice 0) of a single track is read — multi-voice
measures and the bass/drum tracks are out of scope for v1 per the game
plan's non-goals.

Note on `hopo`: PyGuitarPro's NoteEffect.hammer is a single boolean with
no hammer-on vs. pull-off distinction — that direction is normally
inferred by comparing pitch with the previous note (higher = hammer-on,
lower = pull-off). That inference is deferred to Stage 4 (note mapping),
since for M1 the goal is just getting the raw data out correctly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import guitarpro

_SLIDE_IN_TYPES = {guitarpro.SlideType.intoFromAbove, guitarpro.SlideType.intoFromBelow}
_SLIDE_OUT_TYPES = {
    guitarpro.SlideType.shiftSlideTo,
    guitarpro.SlideType.legatoSlideTo,
    guitarpro.SlideType.outDownwards,
    guitarpro.SlideType.outUpwards,
}


def _note_to_ir(note: guitarpro.Note, tick: int, duration_ticks: int, chord_id: int | None) -> dict[str, Any]:
    effect = note.effect
    return {
        "tick": tick,
        "duration_ticks": duration_ticks,
        "pitch": note.realValue,
        "string": note.string,
        "fret": note.value,
        "chord_id": chord_id,
        "hopo": effect.hammer,
        "slide_in": any(s in _SLIDE_IN_TYPES for s in effect.slides),
        "slide_out": any(s in _SLIDE_OUT_TYPES for s in effect.slides),
        "palm_mute": effect.palmMute,
        "dead_note": note.type == guitarpro.NoteType.dead,
        "bend": effect.isBend,
        "tap": isinstance(effect.harmonic, guitarpro.TappedHarmonic),
        "vibrato": effect.vibrato,
        "tremolo_picked": effect.isTremoloPicking,
        "let_ring": effect.letRing,
        "tied": note.type == guitarpro.NoteType.tie,
    }


def list_tracks(path: str | Path) -> list[tuple[int, str]]:
    """Return [(track_index, name), ...] so a caller can pick the right
    one for `dump_ir` — track order/naming isn't consistent enough
    across real files to assume index 0 is always the lead guitar."""
    song = guitarpro.parse(str(path))
    return [(i, track.name) for i, track in enumerate(song.tracks)]


def dump_ir(path: str | Path, track_index: int = 0) -> list[dict[str, Any]]:
    """Return a tick-ordered list of note IR dicts for one track's
    primary voice. `track_index` is 0-based (0 = first track)."""
    song = guitarpro.parse(str(path))
    if not (0 <= track_index < len(song.tracks)):
        raise ValueError(f"track_index {track_index} out of range (song has {len(song.tracks)} tracks)")
    track = song.tracks[track_index]

    notes_ir: list[dict[str, Any]] = []
    chord_counter = 0
    for measure in track.measures:
        voice = measure.voices[0]
        for beat in voice.beats:
            if not beat.notes:
                continue
            chord_id = None
            if len(beat.notes) > 1:
                chord_id = chord_counter
                chord_counter += 1
            for note in beat.notes:
                notes_ir.append(_note_to_ir(note, beat.start, beat.duration.time, chord_id))

    return notes_ir
