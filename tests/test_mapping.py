"""Tests for the note-mapping stage."""

from shred2chart.config import Config
from shred2chart.ir import IRSong, NoteEvent, SectionEvent
from shred2chart.mapping import (
    ChartNote,
    _build_contour_map,
    _calc_sustain,
    _identify_phrases,
    _is_open,
    map_notes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _note(tick, pitch, string=1, fret=1, duration=0, **flags) -> NoteEvent:
    return NoteEvent(
        tick=tick,
        duration_ticks=duration,
        pitch=pitch,
        string=string,
        fret=fret,
        **flags,
    )


def _ir(*notes) -> IRSong:
    ir = IRSong()
    ir.notes.extend(notes)
    return ir


def _cfg(**kwargs) -> Config:
    return Config(**kwargs)


# ---------------------------------------------------------------------------
# _is_open
# ---------------------------------------------------------------------------

def test_is_open_true():
    n = _note(0, 40, string=6, fret=0)
    assert _is_open(n, {6})


def test_is_open_fret_nonzero():
    n = _note(0, 42, string=6, fret=2)
    assert not _is_open(n, {6})


def test_is_open_wrong_string():
    n = _note(0, 40, string=5, fret=0)
    assert not _is_open(n, {6})


# ---------------------------------------------------------------------------
# _build_contour_map
# ---------------------------------------------------------------------------

def test_single_pitch_maps_to_center():
    notes = [_note(0, 50)]
    m = _build_contour_map(notes)
    assert m[50] == 2


def test_two_pitches_symmetric():
    notes = [_note(0, 40), _note(0, 50)]
    m = _build_contour_map(notes)
    assert m[40] < m[50]
    # With 2 unique pitches and offset=(5-2)//2=1 → lanes 1 and 2.
    assert m[40] == 1
    assert m[50] == 2


def test_five_pitches_uses_all_lanes():
    pitches = [40, 43, 46, 50, 54]
    notes = [_note(i * 192, p) for i, p in enumerate(pitches)]
    m = _build_contour_map(notes)
    lanes = sorted(m[p] for p in pitches)
    assert lanes == [0, 1, 2, 3, 4]


def test_more_than_five_pitches_linear():
    pitches = [40, 41, 42, 43, 44, 45, 46]
    notes = [_note(0, p) for p in pitches]
    m = _build_contour_map(notes)
    # All lanes must be in [0, 4].
    for pitch, lane in m.items():
        assert 0 <= lane <= 4
    # Min pitch → lane 0, max pitch → lane 4.
    assert m[40] == 0
    assert m[46] == 4


def test_empty_notes_empty_map():
    assert _build_contour_map([]) == {}


# ---------------------------------------------------------------------------
# _calc_sustain
# ---------------------------------------------------------------------------

def test_sustain_below_threshold_returns_zero():
    cfg = _cfg(sustain_threshold_beats=0.125)
    # 0.125 * 192 = 24 ticks threshold.  Note of 10 ticks → 0.
    assert _calc_sustain(10, 192, cfg) == 0


def test_sustain_above_threshold_positive():
    cfg = _cfg(sustain_threshold_beats=0.125)
    # 192 ticks (1 beat) → should be positive.
    result = _calc_sustain(192, 192, cfg)
    assert result > 0
    assert result < 192  # gap trim applied


def test_sustain_exactly_threshold_is_zero():
    cfg = _cfg(sustain_threshold_beats=0.25)
    threshold = round(0.25 * 192)  # 48 ticks
    assert _calc_sustain(threshold - 1, 192, cfg) == 0


# ---------------------------------------------------------------------------
# _identify_phrases
# ---------------------------------------------------------------------------

def test_no_break_short_gap():
    cfg = _cfg(phrase_boundary_beats=1.0)
    ir = IRSong(resolution=192)
    notes = [
        _note(0, 50, duration=96),    # ends at 96
        _note(100, 55, duration=96),  # gap = 4 ticks < 192 threshold
    ]
    phrases = _identify_phrases(notes, ir, cfg)
    assert len(phrases) == 1


def test_phrase_break_on_large_gap():
    cfg = _cfg(phrase_boundary_beats=1.0)
    ir = IRSong(resolution=192)
    notes = [
        _note(0, 50, duration=96),       # ends at 96
        _note(300, 55, duration=96),     # gap = 204 ticks > 192 threshold
    ]
    phrases = _identify_phrases(notes, ir, cfg)
    assert len(phrases) == 2


def test_phrase_break_at_section_marker():
    cfg = _cfg(phrase_boundary_beats=4.0)  # high threshold so only marker breaks
    ir = IRSong(resolution=192)
    ir.sections.append(SectionEvent(tick=192, name="Chorus"))
    notes = [
        _note(0, 50, duration=48),
        _note(192, 55, duration=48),   # same tick as section marker
    ]
    phrases = _identify_phrases(notes, ir, cfg)
    assert len(phrases) == 2


# ---------------------------------------------------------------------------
# map_notes — open notes
# ---------------------------------------------------------------------------

def test_open_note_string6_fret0():
    cfg = _cfg(open_strings=[6])
    ir = _ir(_note(0, 40, string=6, fret=0, duration=0))
    result = map_notes(ir, cfg)
    assert len(result) == 1
    assert result[0].lane == 7


def test_non_open_note_on_string6_fret1():
    cfg = _cfg(open_strings=[6])
    ir = _ir(_note(0, 42, string=6, fret=1, duration=0))
    result = map_notes(ir, cfg)
    assert len(result) == 1
    assert result[0].lane != 7


def test_open_note_on_string7():
    cfg = _cfg(open_strings=[6, 7])
    ir = _ir(_note(0, 35, string=7, fret=0, duration=0))
    result = map_notes(ir, cfg)
    assert result[0].lane == 7


# ---------------------------------------------------------------------------
# map_notes — HOPO / tap flags
# ---------------------------------------------------------------------------

def test_hammer_on_sets_hopo():
    cfg = _cfg(open_strings=[6])
    note = _note(0, 50, string=1, fret=5, duration=0, hammer_on=True)
    ir = _ir(note)
    result = map_notes(ir, cfg)
    assert result[0].hopo is True


def test_pull_off_sets_hopo():
    cfg = _cfg(open_strings=[6])
    note = _note(0, 50, string=1, fret=5, duration=0, pull_off=True)
    ir = _ir(note)
    result = map_notes(ir, cfg)
    assert result[0].hopo is True


def test_tap_sets_tap_flag():
    cfg = _cfg(open_strings=[6])
    note = _note(0, 50, string=1, fret=9, duration=0, tap=True)
    ir = _ir(note)
    result = map_notes(ir, cfg)
    assert result[0].tap is True


def test_plain_note_no_hopo():
    cfg = _cfg(open_strings=[6])
    note = _note(0, 50, string=1, fret=5, duration=0)
    ir = _ir(note)
    result = map_notes(ir, cfg)
    assert result[0].hopo is False
    assert result[0].tap is False


# ---------------------------------------------------------------------------
# map_notes — repeated pitch stays on same lane
# ---------------------------------------------------------------------------

def test_repeated_pitch_same_lane():
    cfg = _cfg(open_strings=[6])
    # Two notes at the same pitch, different ticks, within one phrase.
    notes = [
        _note(0, 50, string=1, fret=5, duration=48),
        _note(192, 50, string=1, fret=5, duration=48),
    ]
    ir = _ir(*notes)
    result = sorted(map_notes(ir, cfg), key=lambda n: n.tick)
    assert result[0].lane == result[1].lane


# ---------------------------------------------------------------------------
# map_notes — empty input
# ---------------------------------------------------------------------------

def test_empty_ir_returns_empty():
    ir = IRSong()
    result = map_notes(ir, Config())
    assert result == []


# ---------------------------------------------------------------------------
# map_notes — chord lane spread
# ---------------------------------------------------------------------------

def test_chord_two_notes_different_lanes():
    cfg = _cfg(open_strings=[6])
    notes = [
        _note(0, 40, string=2, fret=0, duration=0),
        _note(0, 50, string=1, fret=0, duration=0),
    ]
    ir = _ir(*notes)
    result = map_notes(ir, cfg)
    assert len(result) == 2
    lanes = {cn.lane for cn in result}
    assert len(lanes) == 2, "Chord notes should occupy different lanes"


def test_chord_max_width_respected():
    cfg = _cfg(open_strings=[6], max_chord_width=2)
    # Five notes at different pitches all at tick 0.
    notes = [_note(0, 40 + i * 3, string=i + 1, fret=i, duration=0) for i in range(5)]
    ir = _ir(*notes)
    result = map_notes(ir, cfg)
    lanes = [cn.lane for cn in result]
    assert max(lanes) - min(lanes) <= cfg.max_chord_width
