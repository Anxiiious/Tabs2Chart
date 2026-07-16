"""Tests for the IR data structures."""

from shred2chart.ir import (
    IRSong,
    NoteEvent,
    SectionEvent,
    TempoEvent,
    TimeSignatureEvent,
)


def test_note_event_defaults():
    note = NoteEvent(tick=0, duration_ticks=192, pitch=40, string=6, fret=0)
    assert note.tick == 0
    assert note.duration_ticks == 192
    assert note.pitch == 40
    assert note.string == 6
    assert note.fret == 0
    assert note.chord_id is None
    assert not note.hammer_on
    assert not note.pull_off
    assert not note.tap
    assert not note.slide_out
    assert not note.palm_mute
    assert not note.dead_note
    assert not note.bend
    assert not note.vibrato
    assert not note.tremolo_picked


def test_tempo_event():
    te = TempoEvent(tick=0, bpm=120.0)
    assert te.tick == 0
    assert te.bpm == 120.0
    assert te.linear_ramp_to is None


def test_tempo_event_with_ramp():
    te = TempoEvent(tick=0, bpm=100.0, linear_ramp_to=140.0)
    assert te.linear_ramp_to == 140.0


def test_time_signature_event():
    ts = TimeSignatureEvent(tick=0, numerator=4, denominator=4)
    assert ts.numerator == 4
    assert ts.denominator == 4


def test_section_event():
    se = SectionEvent(tick=768, name="Intro")
    assert se.tick == 768
    assert se.name == "Intro"


def test_ir_song_defaults():
    ir = IRSong()
    assert ir.resolution == 192
    assert ir.string_count == 6
    assert len(ir.tuning) == 6
    assert ir.notes == []
    assert ir.tempo_events == []
    assert ir.time_signatures == []
    assert ir.sections == []


def test_ir_song_standard_tuning():
    ir = IRSong()
    # Standard 6-string: E4 B3 G3 D3 A2 E2
    assert ir.tuning[0] == 64  # E4 (highest string)
    assert ir.tuning[5] == 40  # E2 (lowest string)


def test_ir_song_with_notes():
    ir = IRSong(title="Test Song", artist="Test Artist")
    n = NoteEvent(tick=0, duration_ticks=0, pitch=40, string=6, fret=0)
    ir.notes.append(n)
    assert len(ir.notes) == 1
    assert ir.notes[0].fret == 0


def test_chord_id_shared():
    n1 = NoteEvent(tick=0, duration_ticks=192, pitch=40, string=6, fret=0, chord_id=1)
    n2 = NoteEvent(tick=0, duration_ticks=192, pitch=45, string=5, fret=0, chord_id=1)
    assert n1.chord_id == n2.chord_id
