"""Round-trip tests for PyGuitarPro-based note IR extraction (M1)."""
from __future__ import annotations

import guitarpro as gp

from shred2chart.ir_gp import dump_ir, list_tracks

# PyGuitarPro requires beat.status = normal (and note.type = normal) to
# be set explicitly, or gp.write() silently collapses multiple beats in
# one voice into one on the round trip — a real gotcha in constructing
# test fixtures, not a shred2chart bug. Also, each voice must exactly
# fill its measure's time signature (3840 ticks for 4/4).


def _quarter_note(voice, fret, string=3, hammer=False):
    beat = gp.Beat(voice)
    beat.duration = gp.Duration(value=gp.Duration.quarter)
    beat.status = gp.BeatStatus.normal
    note = gp.Note(beat, value=fret, string=string)
    note.type = gp.NoteType.normal
    note.effect.hammer = hammer
    beat.notes.append(note)
    voice.beats.append(beat)
    return beat


def test_dump_ir_chord_grouping_and_flags(tmp_path):
    song = gp.Song()
    song.tracks[0].name = "Lead Guitar"
    voice = song.tracks[0].measures[0].voices[0]

    single_note_beat = gp.Beat(voice)
    single_note_beat.duration = gp.Duration(value=gp.Duration.half)
    single_note_beat.status = gp.BeatStatus.normal
    solo_note = gp.Note(single_note_beat, value=5, string=3)
    solo_note.type = gp.NoteType.normal
    solo_note.effect.palmMute = True
    single_note_beat.notes.append(solo_note)
    voice.beats.append(single_note_beat)

    chord_beat = gp.Beat(voice)
    chord_beat.duration = gp.Duration(value=gp.Duration.half)  # fills the rest of 4/4
    chord_beat.status = gp.BeatStatus.normal
    root_note = gp.Note(chord_beat, value=7, string=3)
    root_note.type = gp.NoteType.normal
    fifth_note = gp.Note(chord_beat, value=7, string=2)
    fifth_note.type = gp.NoteType.normal
    chord_beat.notes.extend([root_note, fifth_note])
    voice.beats.append(chord_beat)

    out_file = tmp_path / "test.gp5"
    gp.write(song, str(out_file), version=(5, 1, 0))

    assert list_tracks(out_file) == [(0, "Lead Guitar")]

    notes = dump_ir(out_file, track_index=0)
    assert len(notes) == 3

    first = notes[0]
    assert first["tick"] == gp.Duration.quarterTime  # first measure starts at tick 960
    assert first["duration_ticks"] == gp.Duration.quarterTime * 2  # half note
    assert first["fret"] == 5
    assert first["string"] == 3
    assert first["chord_id"] is None
    assert first["palm_mute"] is True

    # Chord note order isn't guaranteed to survive the round trip (GP5
    # seems to re-sort by string), so match by string instead of index.
    chord_notes = notes[1:]
    assert chord_notes[0]["chord_id"] == chord_notes[1]["chord_id"] is not None
    assert chord_notes[0]["tick"] == chord_notes[1]["tick"]


def test_dump_ir_hammer_on_and_pull_off_direction(tmp_path):
    # NoteEffect.hammer is set on the ORIGIN note (confirmed against
    # editor-on-fire's GP importer — see ir_gp.py's module docstring),
    # so the flag on note A actually describes note B, and direction
    # comes from comparing fret numbers: B's fret >= A's -> hammer-on,
    # lower -> pull-off.
    song = gp.Song()
    voice = song.tracks[0].measures[0].voices[0]
    _quarter_note(voice, fret=5, hammer=True)   # A: leads into a HOPO
    _quarter_note(voice, fret=8)                 # B: higher than A -> hammer-on
    _quarter_note(voice, fret=8, hammer=True)   # C: leads into a HOPO
    _quarter_note(voice, fret=3)                 # D: lower than C -> pull-off

    out_file = tmp_path / "test.gp5"
    gp.write(song, str(out_file), version=(5, 1, 0))

    notes = dump_ir(out_file, track_index=0)
    assert len(notes) == 4
    note_a, note_b, note_c, note_d = notes

    assert note_a["hammer_on"] is False and note_a["pull_off"] is False
    assert note_b["hammer_on"] is True and note_b["pull_off"] is False
    assert note_c["hammer_on"] is False and note_c["pull_off"] is False
    assert note_d["hammer_on"] is False and note_d["pull_off"] is True
