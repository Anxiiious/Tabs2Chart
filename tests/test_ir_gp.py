"""Round-trip test for PyGuitarPro-based note IR extraction (M1)."""
from __future__ import annotations

import guitarpro as gp

from shred2chart.ir_gp import dump_ir, list_tracks


def test_dump_ir_round_trip(tmp_path):
    song = gp.Song()
    song.tracks[0].name = "Lead Guitar"
    measure = song.tracks[0].measures[0]
    voice = measure.voices[0]

    # PyGuitarPro requires beat.status = normal (and note.type = normal)
    # to be set explicitly, or gp.write() silently collapses multiple
    # beats in one voice into one on the round trip — a real gotcha in
    # constructing test fixtures, not a shred2chart bug. Also, each
    # voice must exactly fill its measure's time signature (3840 ticks
    # for 4/4), so these two beats are both half notes.
    single_note_beat = gp.Beat(voice)
    single_note_beat.duration = gp.Duration(value=gp.Duration.half)
    single_note_beat.status = gp.BeatStatus.normal
    note = gp.Note(single_note_beat, value=5, string=3)
    note.type = gp.NoteType.normal
    note.effect.palmMute = True
    single_note_beat.notes.append(note)
    voice.beats.append(single_note_beat)

    chord_beat = gp.Beat(voice)
    chord_beat.duration = gp.Duration(value=gp.Duration.half)
    chord_beat.status = gp.BeatStatus.normal
    root_note = gp.Note(chord_beat, value=7, string=3)
    root_note.type = gp.NoteType.normal
    root_note.effect.hammer = True
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
    by_string = {n["string"]: n for n in chord_notes}
    assert by_string[3]["hopo"] is True
    assert by_string[2]["hopo"] is False
