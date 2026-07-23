"""Tests for direct GPIF XML note IR extraction (M1).

The schema exercised here (Bars/Voices/Beats/Notes/Rhythms, and each
Note Property name) was confirmed against two real Sheet Happens tabs —
see shred2chart/ir_gpif.py's module docstring and
SHRED2CHART_GAMEPLAN.md's Current State.
"""
from __future__ import annotations

from shred2chart.gpif_tempo import TICKS_PER_QUARTER
from shred2chart.ir_gpif import dump_ir, list_tracks

GPIF_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<GPIF>
<Tracks>
<Track id="0"><Name><![CDATA[Rhythm Guitar]]></Name></Track>
<Track id="1"><Name><![CDATA[Lead Guitar]]></Name></Track>
</Tracks>
<MasterTrack>
<Tracks>0 1</Tracks>
</MasterTrack>
<MasterBars>
{master_bars}
</MasterBars>
<Bars>
{bars}
</Bars>
<Voices>
{voices}
</Voices>
<Beats>
{beats}
</Beats>
<Rhythms>
<Rhythm id="0"><NoteValue>Quarter</NoteValue></Rhythm>
</Rhythms>
<Notes>
{notes}
</Notes>
</GPIF>
"""


def _xml(track1_bar_id, track1_voices="0 -1 -1 -1"):
    master_bars = f'<MasterBar><Time>4/4</Time><Bars>99 {track1_bar_id}</Bars></MasterBar>'
    bars = (
        '<Bar id="99"><Voices>-1 -1 -1 -1</Voices></Bar>'
        f'<Bar id="{track1_bar_id}"><Voices>{track1_voices}</Voices></Bar>'
    )
    return master_bars, bars


def test_dump_ir_single_notes_and_chord_and_techniques():
    master_bars, bars = _xml(track1_bar_id=1)
    voices = '<Voice id="0"><Beats>0 1 2</Beats></Voice>'
    beats = (
        '<Beat id="0"><Rhythm ref="0" /><Notes>0</Notes></Beat>'
        '<Beat id="1"></Beat>'  # a rest: no <Notes> at all
        '<Beat id="2"><Rhythm ref="0" /><Notes>1 2</Notes></Beat>'
    )
    # Beat 1 (the rest) is missing its Rhythm ref in this fixture on purpose
    # to prove rests are skippable without one — patch it back in properly:
    beats = beats.replace('<Beat id="1"></Beat>', '<Beat id="1"><Rhythm ref="0" /></Beat>')
    notes = """
<Note id="0">
<Tie origin="false" destination="true" />
<Accent>1</Accent>
<Properties>
<Property name="Fret"><Fret>5</Fret></Property>
<Property name="Midi"><Number>60</Number></Property>
<Property name="String"><String>2</String></Property>
<Property name="HopoDestination"><Enable /></Property>
<Property name="Tapped"><Enable /></Property>
</Properties>
</Note>
<Note id="1">
<Properties>
<Property name="Fret"><Fret>7</Fret></Property>
<Property name="Midi"><Number>62</Number></Property>
<Property name="String"><String>1</String></Property>
</Properties>
</Note>
<Note id="2">
<Properties>
<Property name="Fret"><Fret>9</Fret></Property>
<Property name="Midi"><Number>64</Number></Property>
<Property name="String"><String>2</String></Property>
<Property name="Slide"><Flags>2</Flags></Property>
<Property name="PalmMuted"><Enable /></Property>
</Properties>
</Note>
"""
    xml_text = GPIF_TEMPLATE.format(master_bars=master_bars, bars=bars, voices=voices, beats=beats, notes=notes)

    assert list_tracks(xml_text) == [(0, "Rhythm Guitar"), (1, "Lead Guitar")]

    result = dump_ir(xml_text, track_index=1)
    assert len(result) == 3  # rest beat contributes nothing

    first = result[0]
    assert first["tick"] == 0
    assert first["duration_ticks"] == TICKS_PER_QUARTER
    assert first["fret"] == 5
    assert first["string"] == 3  # GPIF's 0-based 2 -> our 1-based 3
    assert first["chord_id"] is None
    assert first["tied"] is True
    # No previous note to compare against, so a destination note with no
    # prior context defaults to hammer-on (matches ir_gp.py's convention
    # of "no previous note means no inherited HOPO" landing on the same
    # not-a-pull-off side).
    assert first["hammer_on"] is True
    assert first["pull_off"] is False
    assert first["tap"] is True
    assert first["accent"] is True
    assert first["ghost_note"] is False  # no GhostNote property on this note

    # Beat 1 was a rest (still advances the clock by one quarter note),
    # so the chord in beat 2 starts a full quarter note after beat 0.
    chord_a, chord_b = result[1], result[2]
    assert chord_a["tick"] == chord_b["tick"] == 2 * TICKS_PER_QUARTER
    assert chord_a["chord_id"] == chord_b["chord_id"] is not None
    assert chord_b["slide_out"] is True  # Flags=2 is a legato slide-out
    assert chord_b["slide_in"] is False
    assert chord_b["slide_flags"] == 2
    assert chord_b["palm_mute"] is True


def test_dump_ir_skips_bar_with_no_voice_for_this_track():
    # track 1 has voice slot -1 (nothing) in its only bar
    master_bars, bars = _xml(track1_bar_id=1, track1_voices="-1 -1 -1 -1")
    xml_text = GPIF_TEMPLATE.format(
        master_bars=master_bars,
        bars=bars,
        voices="",
        beats="",
        notes="",
    )
    assert dump_ir(xml_text, track_index=1) == []


def test_ghost_note_property_detected():
    """A note with <Property name="GhostNote"><Enable/></Property> should
    produce ghost_note=True; a note without it should produce ghost_note=False.
    Property name inferred from GPIF's uniform Enable-flag pattern — unverified
    against a real file with ghost notes, but the implementation is testable
    with a synthetic fixture."""
    master_bars, bars = _xml(track1_bar_id=1)
    voices = '<Voice id="0"><Beats>0 1</Beats></Voice>'
    beats = (
        '<Beat id="0"><Rhythm ref="0" /><Notes>0</Notes></Beat>'
        '<Beat id="1"><Rhythm ref="0" /><Notes>1</Notes></Beat>'
    )
    notes = """
<Note id="0">
<Properties>
<Property name="Fret"><Fret>5</Fret></Property>
<Property name="Midi"><Number>60</Number></Property>
<Property name="String"><String>0</String></Property>
<Property name="GhostNote"><Enable /></Property>
</Properties>
</Note>
<Note id="1">
<Properties>
<Property name="Fret"><Fret>7</Fret></Property>
<Property name="Midi"><Number>62</Number></Property>
<Property name="String"><String>0</String></Property>
</Properties>
</Note>
"""
    xml_text = GPIF_TEMPLATE.format(
        master_bars=master_bars, bars=bars, voices=voices, beats=beats, notes=notes
    )
    result = dump_ir(xml_text, track_index=1)
    assert result[0]["ghost_note"] is True
    assert result[1]["ghost_note"] is False
