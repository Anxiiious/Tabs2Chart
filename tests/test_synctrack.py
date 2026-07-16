"""Tests for the SyncTrack builder."""

from shred2chart.ir import IRSong, TempoEvent, TimeSignatureEvent
from shred2chart.synctrack import SyncEvent, build_synctrack, _bpm_to_millibpm, _denom_exp


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_ir(**kwargs) -> IRSong:
    ir = IRSong(**kwargs)
    return ir


# ---------------------------------------------------------------------------
# _bpm_to_millibpm
# ---------------------------------------------------------------------------

def test_bpm_to_millibpm_120():
    assert _bpm_to_millibpm(120.0) == 120_000


def test_bpm_to_millibpm_fractional():
    assert _bpm_to_millibpm(99.5) == 99_500


# ---------------------------------------------------------------------------
# _denom_exp
# ---------------------------------------------------------------------------

def test_denom_exp_4():
    assert _denom_exp(4) == 2


def test_denom_exp_8():
    assert _denom_exp(8) == 3


def test_denom_exp_2():
    assert _denom_exp(2) == 1


def test_denom_exp_16():
    assert _denom_exp(16) == 4


def test_denom_exp_invalid_zero():
    # Edge-case: guard against log2(0) crash.
    assert _denom_exp(0) == 2


# ---------------------------------------------------------------------------
# build_synctrack — baseline
# ---------------------------------------------------------------------------

def test_empty_ir_always_has_anchor_events():
    ir = _make_ir()
    events = build_synctrack(ir)
    # Must have at least a B event and a TS event at tick 0.
    kinds = {(ev.tick, ev.kind) for ev in events}
    assert (0, "B") in kinds
    assert (0, "TS") in kinds


def test_single_tempo_event():
    ir = _make_ir()
    ir.tempo_events.append(TempoEvent(tick=0, bpm=120.0))
    events = build_synctrack(ir)
    b_events = [e for e in events if e.kind == "B"]
    # Exactly one B event at tick 0 with 120 000 milliBPM.
    assert len(b_events) == 1
    assert b_events[0].tick == 0
    assert b_events[0].values[0] == 120_000


def test_tempo_change_mid_song():
    ir = _make_ir()
    ir.tempo_events.append(TempoEvent(tick=0, bpm=120.0))
    ir.tempo_events.append(TempoEvent(tick=768, bpm=140.0))
    events = build_synctrack(ir)
    b_events = sorted([e for e in events if e.kind == "B"], key=lambda e: e.tick)
    assert b_events[0].values[0] == 120_000
    assert b_events[1].tick == 768
    assert b_events[1].values[0] == 140_000


def test_time_sig_4_4():
    ir = _make_ir()
    ir.time_signatures.append(TimeSignatureEvent(tick=0, numerator=4, denominator=4))
    events = build_synctrack(ir)
    ts_events = [e for e in events if e.kind == "TS"]
    # There may be a default TS plus ours; check ours is present.
    matching = [e for e in ts_events if e.tick == 0 and e.values == (4, 2)]
    assert matching, f"Expected (4, 2) TS at tick 0; got {ts_events}"


def test_time_sig_6_8():
    ir = _make_ir()
    ir.time_signatures.append(TimeSignatureEvent(tick=0, numerator=6, denominator=8))
    events = build_synctrack(ir)
    ts_events = [e for e in events if e.kind == "TS"]
    matching = [e for e in ts_events if e.values == (6, 3)]
    assert matching, f"Expected (6, 3) TS; got {ts_events}"


def test_time_sig_3_4():
    ir = _make_ir()
    ir.time_signatures.append(TimeSignatureEvent(tick=0, numerator=3, denominator=4))
    events = build_synctrack(ir)
    ts_events = [e for e in events if e.kind == "TS" and e.tick == 0]
    # Deduplicated: should end up with (3, 2).
    assert any(e.values == (3, 2) for e in ts_events), ts_events


def test_sort_order_b_before_ts_same_tick():
    ir = _make_ir()
    ir.tempo_events.append(TempoEvent(tick=0, bpm=100.0))
    ir.time_signatures.append(TimeSignatureEvent(tick=0, numerator=4, denominator=4))
    events = build_synctrack(ir)
    same_tick = [e for e in events if e.tick == 0]
    kinds_order = [e.kind for e in same_tick]
    b_idx = kinds_order.index("B")
    ts_idx = kinds_order.index("TS")
    assert b_idx < ts_idx


def test_linear_ramp_emits_multiple_b_events():
    ir = _make_ir(resolution=192)
    # Ramp from 100 to 140 BPM over 4 beats (one measure of 4/4).
    ir.tempo_events.append(
        TempoEvent(tick=0, bpm=100.0, linear_ramp_to=140.0)
    )
    ir.tempo_events.append(TempoEvent(tick=768, bpm=140.0))
    events = build_synctrack(ir)
    b_events = [e for e in events if e.kind == "B" and e.tick < 768]
    # Should have multiple B events within the ramp span.
    assert len(b_events) >= 2


def test_deduplication_keeps_later_event():
    ir = _make_ir()
    # Two tempo events at tick 0 — the later one (140) should win.
    ir.tempo_events.append(TempoEvent(tick=0, bpm=120.0))
    ir.tempo_events.append(TempoEvent(tick=0, bpm=140.0))
    events = build_synctrack(ir)
    b_at_0 = [e for e in events if e.kind == "B" and e.tick == 0]
    assert len(b_at_0) == 1
    assert b_at_0[0].values[0] == 140_000


def test_no_b_at_nonzero_start_produces_anchor():
    """If tempo_events only contains events after tick 0, anchor is inserted."""
    ir = _make_ir()
    ir.tempo_events.append(TempoEvent(tick=192, bpm=130.0))
    events = build_synctrack(ir)
    b_at_0 = [e for e in events if e.kind == "B" and e.tick == 0]
    assert b_at_0, "Expected a B anchor event at tick 0"
