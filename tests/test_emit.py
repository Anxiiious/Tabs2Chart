"""Tests for the .chart and song.ini emitter."""

import re

from shred2chart.config import Config
from shred2chart.emit import build_chart, build_song_ini, _section_synctrack, _section_expert_single
from shred2chart.ir import IRSong, SectionEvent, TempoEvent, TimeSignatureEvent
from shred2chart.mapping import ChartNote
from shred2chart.synctrack import SyncEvent, build_synctrack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ir(**kwargs) -> IRSong:
    defaults = dict(title="Test Song", artist="Test Artist", resolution=192)
    defaults.update(kwargs)
    return IRSong(**defaults)


def _basic_sync() -> list:
    return [
        SyncEvent(tick=0, kind="B", values=(120_000,)),
        SyncEvent(tick=0, kind="TS", values=(4, 2)),
    ]


# ---------------------------------------------------------------------------
# [Song] section
# ---------------------------------------------------------------------------

def test_chart_has_song_section():
    ir = _make_ir()
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert "[Song]" in chart


def test_chart_song_title():
    ir = _make_ir(title="Bleed")
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert 'Name = "Bleed"' in chart


def test_chart_song_artist():
    ir = _make_ir(artist="Meshuggah")
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert 'Artist = "Meshuggah"' in chart


def test_chart_resolution():
    ir = _make_ir(resolution=192)
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert "Resolution = 192" in chart


def test_chart_offset_zero():
    cfg = Config(offset_ms=0)
    ir = _make_ir()
    chart = build_chart(ir, _basic_sync(), [], cfg)
    assert "Offset = 0.000" in chart


def test_chart_offset_nonzero():
    cfg = Config(offset_ms=-150)
    ir = _make_ir()
    chart = build_chart(ir, _basic_sync(), [], cfg)
    assert "Offset = -0.150" in chart


# ---------------------------------------------------------------------------
# [SyncTrack] section
# ---------------------------------------------------------------------------

def test_chart_has_synctrack_section():
    ir = _make_ir()
    sync = build_synctrack(ir)
    chart = build_chart(ir, sync, [], Config())
    assert "[SyncTrack]" in chart


def test_synctrack_b_event_format():
    sync = [SyncEvent(tick=0, kind="B", values=(120_000,))]
    text = _section_synctrack(sync)
    assert "0 = B 120000" in text


def test_synctrack_ts_44_omits_denom():
    sync = [SyncEvent(tick=0, kind="TS", values=(4, 2))]
    text = _section_synctrack(sync)
    # Default denominator exp (2) is omitted.
    assert "0 = TS 4" in text
    # Must NOT have an explicit '2' after TS 4.
    assert "TS 4 2" not in text


def test_synctrack_ts_68_includes_denom():
    sync = [SyncEvent(tick=0, kind="TS", values=(6, 3))]
    text = _section_synctrack(sync)
    assert "0 = TS 6 3" in text


# ---------------------------------------------------------------------------
# [Events] section
# ---------------------------------------------------------------------------

def test_chart_has_events_section():
    ir = _make_ir()
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert "[Events]" in chart


def test_section_marker_emitted():
    ir = _make_ir()
    ir.sections.append(SectionEvent(tick=768, name="Chorus"))
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert '768 = E "section Chorus"' in chart


def test_section_name_quotes_escaped():
    ir = _make_ir()
    ir.sections.append(SectionEvent(tick=0, name='He said "hello"'))
    chart = build_chart(ir, _basic_sync(), [], Config())
    # Double-quotes in the name are replaced with single-quotes.
    assert '"He said' not in chart.split('"section')[1].split('"')[0]


# ---------------------------------------------------------------------------
# [ExpertSingle] section — note format
# ---------------------------------------------------------------------------

def test_chart_has_expert_single_section():
    ir = _make_ir()
    chart = build_chart(ir, _basic_sync(), [], Config())
    assert "[ExpertSingle]" in chart


def test_note_green_format():
    notes = [ChartNote(tick=0, lane=0, duration_ticks=0)]
    text = _section_expert_single(notes)
    assert "0 = N 0 0" in text


def test_note_orange_format():
    notes = [ChartNote(tick=192, lane=4, duration_ticks=100)]
    text = _section_expert_single(notes)
    assert "192 = N 4 100" in text


def test_open_note_format():
    notes = [ChartNote(tick=0, lane=7, duration_ticks=0)]
    text = _section_expert_single(notes)
    assert "0 = N 7 0" in text


def test_hopo_flag_emitted_on_same_tick():
    notes = [ChartNote(tick=0, lane=2, duration_ticks=0, hopo=True)]
    text = _section_expert_single(notes)
    assert "0 = N 2 0" in text
    assert "0 = N 5 0" in text


def test_tap_flag_emitted_instead_of_hopo():
    notes = [ChartNote(tick=0, lane=2, duration_ticks=0, tap=True)]
    text = _section_expert_single(notes)
    assert "0 = N 6 0" in text
    # tap takes priority; N 5 must NOT also appear.
    assert "0 = N 5 0" not in text


def test_tap_and_hopo_both_true_emits_only_tap():
    notes = [ChartNote(tick=0, lane=2, duration_ticks=0, hopo=True, tap=True)]
    text = _section_expert_single(notes)
    assert "0 = N 6 0" in text
    assert "0 = N 5 0" not in text


def test_no_flag_when_plain_note():
    notes = [ChartNote(tick=0, lane=0, duration_ticks=0)]
    text = _section_expert_single(notes)
    assert "N 5" not in text
    assert "N 6" not in text


def test_notes_sorted_by_tick():
    notes = [
        ChartNote(tick=384, lane=1, duration_ticks=0),
        ChartNote(tick=0, lane=0, duration_ticks=0),
    ]
    text = _section_expert_single(notes)
    idx_0 = text.index("0 = N 0")
    idx_384 = text.index("384 = N 1")
    assert idx_0 < idx_384


# ---------------------------------------------------------------------------
# song.ini
# ---------------------------------------------------------------------------

def test_song_ini_has_name():
    ir = _make_ir(title="Bleed")
    ini = build_song_ini(ir, Config())
    assert "name = Bleed" in ini


def test_song_ini_has_artist():
    ir = _make_ir(artist="Meshuggah")
    ini = build_song_ini(ir, Config())
    assert "artist = Meshuggah" in ini


def test_song_ini_has_delay():
    cfg = Config(offset_ms=42)
    ir = _make_ir()
    ini = build_song_ini(ir, cfg)
    assert "delay = 42" in ini


def test_song_ini_has_charter():
    cfg = Config(charter="my_charter")
    ir = _make_ir()
    ini = build_song_ini(ir, cfg)
    assert "charter = my_charter" in ini


def test_song_ini_starts_with_song_section():
    ir = _make_ir()
    ini = build_song_ini(ir, Config())
    assert ini.startswith("[song]")


# ---------------------------------------------------------------------------
# Integration: full build_chart output is well-formed
# ---------------------------------------------------------------------------

def test_full_chart_section_order():
    ir = _make_ir()
    ir.sections.append(SectionEvent(tick=0, name="Intro"))
    sync = build_synctrack(ir)
    notes = [
        ChartNote(tick=0, lane=0, duration_ticks=0),
        ChartNote(tick=192, lane=7, duration_ticks=0, hopo=True),
    ]
    chart = build_chart(ir, sync, notes, Config())

    # All four required sections present.
    for section in ("[Song]", "[SyncTrack]", "[Events]", "[ExpertSingle]"):
        assert section in chart

    # Sections appear in canonical order.
    order = [chart.index(s) for s in ("[Song]", "[SyncTrack]", "[Events]", "[ExpertSingle]")]
    assert order == sorted(order)
