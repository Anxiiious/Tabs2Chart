"""Integration tests for the ingest stage using real PyGuitarPro round-trips.

These tests write a minimal .gp5 file via the PyGuitarPro API, then parse it
with shred2chart.ingest to verify the IR extraction logic.
"""

import tempfile
import os
import pytest

try:
    import guitarpro
    import guitarpro.models as gpm
    HAS_GP = True
except ImportError:
    HAS_GP = False

pytestmark = pytest.mark.skipif(not HAS_GP, reason="PyGuitarPro not installed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_minimal_song(bpm=120, title="Test", artist="Artist"):
    """Return a minimal Song with one measure of standard 6-string notes."""
    song = guitarpro.Song()
    song.title = title
    song.artist = artist
    song.tempo = bpm

    track = song.tracks[0]
    track.name = "Lead Guitar"
    measure = track.measures[0]
    voice = measure.voices[0]

    # Two quarter notes: open string 6 then fret 5 on string 6.
    b1 = gpm.Beat(voice)
    b1.start = measure.start
    b1.duration = gpm.Duration(value=4)
    n1 = gpm.Note(beat=b1, string=6, value=0, type=gpm.NoteType.normal)
    b1.notes = [n1]

    b2 = gpm.Beat(voice)
    b2.start = measure.start + gpm.Duration.quarterTime
    b2.duration = gpm.Duration(value=4)
    n2 = gpm.Note(beat=b2, string=6, value=5, type=gpm.NoteType.normal)
    n2.effect.hammer = True
    b2.notes = [n2]

    voice.beats = [b1, b2]
    return song


def _write_and_parse(song, config=None):
    """Write *song* to a tmp .gp5 file, then parse it with shred2chart."""
    from shred2chart.ingest import parse_gp_file
    from shred2chart.config import Config

    if config is None:
        config = Config()

    with tempfile.NamedTemporaryFile(suffix=".gp5", delete=False) as fh:
        tmp = fh.name
    try:
        guitarpro.write(song, tmp)
        return parse_gp_file(tmp, config)
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# Basic parse tests
# ---------------------------------------------------------------------------

def test_title_and_artist():
    song = _build_minimal_song(title="Bleed", artist="Meshuggah")
    ir = _write_and_parse(song)
    assert ir.title == "Bleed"
    assert ir.artist == "Meshuggah"


def test_resolution_is_192():
    ir = _write_and_parse(_build_minimal_song())
    assert ir.resolution == 192


def test_initial_tempo_extracted():
    song = _build_minimal_song(bpm=160)
    ir = _write_and_parse(song)
    assert len(ir.tempo_events) >= 1
    assert ir.tempo_events[0].bpm == 160.0
    assert ir.tempo_events[0].tick == 0


def test_time_signature_4_4():
    ir = _write_and_parse(_build_minimal_song())
    assert len(ir.time_signatures) >= 1
    ts = ir.time_signatures[0]
    assert ts.numerator == 4
    assert ts.denominator == 4


def test_string_count_6():
    ir = _write_and_parse(_build_minimal_song())
    assert ir.string_count == 6


def test_standard_tuning():
    ir = _write_and_parse(_build_minimal_song())
    # Standard tuning: E4(64) B3(59) G3(55) D3(50) A2(45) E2(40)
    assert ir.tuning == [64, 59, 55, 50, 45, 40]


# ---------------------------------------------------------------------------
# Note parsing
# ---------------------------------------------------------------------------

def test_notes_parsed():
    ir = _write_and_parse(_build_minimal_song())
    # At least 2 notes should be extracted.
    assert len(ir.notes) >= 2


def test_open_note_fret_zero():
    ir = _write_and_parse(_build_minimal_song())
    open_notes = [n for n in ir.notes if n.string == 6 and n.fret == 0]
    assert open_notes, "Expected at least one open-string note on string 6"


def test_hammer_on_flag():
    ir = _write_and_parse(_build_minimal_song())
    hopo_notes = [n for n in ir.notes if n.hammer_on]
    assert hopo_notes, "Expected at least one hammer-on note"


def test_note_pitch_correct():
    ir = _write_and_parse(_build_minimal_song())
    # String 6 open = E2 = MIDI 40 in standard tuning.
    open_note = next((n for n in ir.notes if n.string == 6 and n.fret == 0), None)
    assert open_note is not None
    assert open_note.pitch == 40


def test_note_pitch_fret5_string6():
    ir = _write_and_parse(_build_minimal_song())
    # String 6 fret 5 = A2 = MIDI 40 + 5 = 45.
    fret5 = next((n for n in ir.notes if n.string == 6 and n.fret == 5), None)
    assert fret5 is not None
    assert fret5.pitch == 45


def test_notes_sorted_by_tick():
    ir = _write_and_parse(_build_minimal_song())
    ticks = [n.tick for n in ir.notes]
    assert ticks == sorted(ticks)


# ---------------------------------------------------------------------------
# dump_tempo_events
# ---------------------------------------------------------------------------

def test_dump_tempo_events():
    from shred2chart.ingest import dump_tempo_events
    song = _build_minimal_song(bpm=140)

    with tempfile.NamedTemporaryFile(suffix=".gp5", delete=False) as fh:
        tmp = fh.name
    try:
        guitarpro.write(song, tmp)
        events = dump_tempo_events(tmp)
    finally:
        os.unlink(tmp)

    assert isinstance(events, list)
    assert len(events) >= 1
    # First entry should reference the initial tempo.
    initial = next((e for e in events if e.get("source") == "song.tempo"), None)
    assert initial is not None
    assert initial["bpm"] == 140.0


# ---------------------------------------------------------------------------
# Custom tuning override
# ---------------------------------------------------------------------------

def test_custom_tuning_override():
    from shred2chart.config import Config
    # Drop D: lower string 6 by 2 semitones.
    drop_d = [64, 59, 55, 50, 45, 38]  # E2 -> D2
    cfg = Config(tuning=drop_d)
    ir = _write_and_parse(_build_minimal_song(), config=cfg)
    assert ir.tuning == drop_d
    # String 6 open should now be MIDI 38 (D2).
    open_note = next((n for n in ir.notes if n.string == 6 and n.fret == 0), None)
    assert open_note is not None
    assert open_note.pitch == 38
