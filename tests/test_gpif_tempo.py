"""Tests for direct GPIF XML tempo/time-signature extraction.

The schema asserted here (MasterTrack/Automations/Automation with
Type=Tempo, and per-bar MasterBar/Time) was confirmed against a real
Sheet Happens tab's score.gpif, not guessed — see
shred2chart/gpif_tempo.py's module docstring and
SHRED2CHART_GAMEPLAN.md's Current State.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from shred2chart.gpif_tempo import (
    TICKS_PER_QUARTER,
    GpifFormatError,
    compute_bar_grid,
    compute_playback_order,
    dump_sections,
    dump_tempo_events,
)

GPIF_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<GPIF>
<Score></Score>
<MasterTrack>
<Automations>
{automations}
</Automations>
</MasterTrack>
<MasterBars>
{master_bars}
</MasterBars>
</GPIF>
"""

AUTOMATION_TEMPLATE = """<Automation>
<Type>Tempo</Type>
<Linear>false</Linear>
<Bar>{bar}</Bar>
<Position>{position}</Position>
<Visible>true</Visible>
<Value>{bpm} 2</Value>
</Automation>"""

LINEAR_AUTOMATION_TEMPLATE = """<Automation>
<Type>Tempo</Type>
<Linear>true</Linear>
<Bar>{bar}</Bar>
<Position>{position}</Position>
<Visible>true</Visible>
<Value>{bpm} 2</Value>
</Automation>"""


def _bar(time=None):
    time_xml = f"<Time>{time}</Time>" if time else ""
    return f"<MasterBar>{time_xml}<Bars>0</Bars></MasterBar>"


def test_constant_tempo_matches_real_file_shape():
    xml_text = GPIF_TEMPLATE.format(
        automations=AUTOMATION_TEMPLATE.format(bar=0, position=0, bpm=123),
        master_bars="".join(_bar("4/4") for _ in range(4)),
    )
    events = dump_tempo_events(xml_text)
    assert events == [
        {"tick": 0, "type": "time_signature", "numerator": 4, "denominator": 4},
        {"tick": 0, "type": "tempo", "bpm": 123},
    ]


def test_time_signature_change_mid_song():
    bars = [_bar("4/4"), _bar(), _bar("3/4"), _bar()]
    xml_text = GPIF_TEMPLATE.format(automations="", master_bars="".join(bars))
    events = dump_tempo_events(xml_text)
    ts_events = [e for e in events if e["type"] == "time_signature"]
    assert ts_events == [
        {"tick": 0, "type": "time_signature", "numerator": 4, "denominator": 4},
        {
            "tick": 2 * TICKS_PER_QUARTER * 4,  # two 4/4 bars precede the change
            "type": "time_signature",
            "numerator": 3,
            "denominator": 4,
        },
    ]


def test_tempo_change_at_start_of_second_bar():
    xml_text = GPIF_TEMPLATE.format(
        automations=(
            AUTOMATION_TEMPLATE.format(bar=0, position=0, bpm=140)
            + AUTOMATION_TEMPLATE.format(bar=1, position=0, bpm=90)
        ),
        master_bars="".join(_bar("4/4") for _ in range(2)),
    )
    events = dump_tempo_events(xml_text)
    tempos = [e for e in events if e["type"] == "tempo"]
    assert tempos == [
        {"tick": 0, "type": "tempo", "bpm": 140},
        {"tick": TICKS_PER_QUARTER * 4, "type": "tempo", "bpm": 90},
    ]


def test_linear_ramp_produces_per_beat_events():
    """A Linear=true automation spanning 2 bars (8 quarter-note beats in 4/4)
    should emit 8 stepped events interpolating from 100 to 140 bpm, with the
    endpoint event emitted separately as the following non-linear automation.
    """
    # Bar 0: linear ramp starts at 100 bpm; bar 2: ramp ends (instant) at 140 bpm.
    xml_text = GPIF_TEMPLATE.format(
        automations=(
            LINEAR_AUTOMATION_TEMPLATE.format(bar=0, position=0, bpm=100)
            + AUTOMATION_TEMPLATE.format(bar=2, position=0, bpm=140)
        ),
        master_bars="".join(_bar("4/4") for _ in range(4)),
    )
    events = dump_tempo_events(xml_text)
    tempos = [e for e in events if e["type"] == "tempo"]

    # The ramp spans bars 0-1 (2 × 4 beats = 8 beats of TICKS_PER_QUARTER each).
    ramp_end_tick = 2 * TICKS_PER_QUARTER * 4
    ramp_events = [e for e in tempos if e["tick"] < ramp_end_tick]
    assert len(ramp_events) == 8  # one per beat

    # First ramp event is at tick 0 with start bpm.
    assert ramp_events[0] == {"tick": 0, "type": "tempo", "bpm": 100}

    # Events are in tick order and spaced exactly one beat apart.
    for i, ev in enumerate(ramp_events):
        assert ev["tick"] == i * TICKS_PER_QUARTER

    # bpm increases monotonically across the ramp.
    bpms = [e["bpm"] for e in ramp_events]
    assert bpms == sorted(bpms)
    assert bpms[0] == 100
    # The ramp covers [start_tick, end_tick) — beat 7 of 8 has frac = 7/8,
    # so its BPM is 100 + 7/8 * 40 = 135.  The endpoint 140 is NOT in the
    # ramp; it comes from the following automation's own event (see below).
    assert bpms[-1] == pytest.approx(135.0)

    # The endpoint automation is present as a normal instant event.
    end_events = [e for e in tempos if e["tick"] == ramp_end_tick]
    assert end_events == [{"tick": ramp_end_tick, "type": "tempo", "bpm": 140}]


def test_linear_ramp_last_automation_falls_back_to_single_event():
    """A Linear=true automation with no following automation has no known
    ramp endpoint — it should produce a single instantaneous event."""
    xml_text = GPIF_TEMPLATE.format(
        automations=LINEAR_AUTOMATION_TEMPLATE.format(bar=0, position=0, bpm=120),
        master_bars="".join(_bar("4/4") for _ in range(2)),
    )
    events = dump_tempo_events(xml_text)
    tempos = [e for e in events if e["type"] == "tempo"]
    assert tempos == [{"tick": 0, "type": "tempo", "bpm": 120}]


def _rbar(*, time=None, repeat=None, alt=None, section=None, target=None, jump=None):
    """A <MasterBar> with optional Time, Repeat, AlternateEndings, Section,
    and Directions (target="Segno"/"Coda", jump="DaCoda"/"DaSegnoAlCoda").

    `repeat` is (start, end, count) with start/end as bools.
    """
    parts = []
    if time:
        parts.append(f"<Time>{time}</Time>")
    if repeat is not None:
        start, end, count = repeat
        parts.append(
            f'<Repeat start="{str(start).lower()}" end="{str(end).lower()}" count="{count}" />'
        )
    if alt is not None:
        parts.append(f"<AlternateEndings>{alt}</AlternateEndings>")
    if section is not None:
        parts.append(f"<Section><Text><![CDATA[{section}]]></Text></Section>")
    if target is not None or jump is not None:
        directions = []
        if target is not None:
            directions.append(f"<Target>{target}</Target>")
        if jump is not None:
            directions.append(f"<Jump>{jump}</Jump>")
        parts.append(f"<Directions>{''.join(directions)}</Directions>")
    parts.append("<Bars>0</Bars>")
    return f"<MasterBar>{''.join(parts)}</MasterBar>"


def _order(bars):
    xml = GPIF_TEMPLATE.format(automations="", master_bars="".join(bars))
    return compute_playback_order(ET.fromstring(xml))


def test_no_repeats_is_written_order():
    assert _order([_rbar(time="4/4"), _rbar(), _rbar()]) == [0, 1, 2]


def test_one_bar_repeated_twice():
    # bar 0: start+end, count=2 -> played twice, matching the tab's x1 markers.
    assert _order([_rbar(time="4/4", repeat=(True, True, 2)), _rbar()]) == [0, 0, 1]


def test_one_bar_repeated_three_times():
    assert _order([_rbar(time="4/4", repeat=(True, True, 3)), _rbar()]) == [0, 0, 0, 1]


def test_multi_bar_repeat():
    bars = [
        _rbar(time="4/4", repeat=(True, False, 2)),  # 0: start
        _rbar(),                                     # 1
        _rbar(repeat=(False, True, 2)),              # 2: end
        _rbar(),                                     # 3
    ]
    assert _order(bars) == [0, 1, 2, 0, 1, 2, 3]


def test_first_and_second_endings():
    # 0 start; 1 body; 2 = 1st ending (repeat-end, alt 1); 3 = 2nd ending (alt 2).
    bars = [
        _rbar(time="4/4", repeat=(True, False, 2)),   # 0
        _rbar(),                                      # 1
        _rbar(repeat=(False, True, 2), alt=1),        # 2 (1st ending)
        _rbar(alt=2),                                 # 3 (2nd ending)
        _rbar(),                                      # 4
    ]
    # pass 1: 0,1,2(1st) -> loop; pass 2: 0,1, skip 2, 3(2nd); then 4.
    assert _order(bars) == [0, 1, 2, 0, 1, 3, 4]


def test_malformed_dangling_repeat_end_does_not_hang():
    # A repeat-end with no matching start loops back to bar 0 each time; the
    # bar cap must trip a clean error rather than spin forever.
    bars = [_rbar(time="4/4"), _rbar(repeat=(False, True, 99))]
    with pytest.raises(GpifFormatError):
        _order(bars)


def test_grid_places_repeated_bar_at_distinct_ticks():
    bars = [_rbar(time="4/4", repeat=(True, True, 2)), _rbar()]
    bar_starts, sigs, source = compute_bar_grid(ET.fromstring(
        GPIF_TEMPLATE.format(automations="", master_bars="".join(bars))
    ))
    one_bar = TICKS_PER_QUARTER * 4
    assert source == [0, 0, 1]                       # bar 0 played twice
    assert bar_starts == [0, one_bar, 2 * one_bar]   # each pass at its own tick
    assert sigs == [(4, 4), (4, 4), (4, 4)]


def test_section_on_repeated_bar_marked_once_at_first_pass():
    bars = [
        _rbar(time="4/4", repeat=(True, True, 2), section="[A] (0:00)"),
        _rbar(),
    ]
    xml = GPIF_TEMPLATE.format(automations="", master_bars="".join(bars))
    sections = dump_sections(xml)
    assert sections == [{"tick": 0, "bar": 0, "name": "[A] (0:00)"}]


def test_tempo_inside_repeat_reapplies_each_pass():
    # A tempo automation on the repeated bar 0 should fire on every pass.
    bars = [_rbar(time="4/4", repeat=(True, True, 2)), _rbar()]
    xml = GPIF_TEMPLATE.format(
        automations=AUTOMATION_TEMPLATE.format(bar=0, position=0, bpm=123),
        master_bars="".join(bars),
    )
    tempos = [e for e in dump_tempo_events(xml) if e["type"] == "tempo"]
    one_bar = TICKS_PER_QUARTER * 4
    assert tempos == [
        {"tick": 0, "type": "tempo", "bpm": 123},
        {"tick": one_bar, "type": "tempo", "bpm": 123},
    ]


# --- D.S. al Coda navigation -------------------------------------------------
#
# Confirmed against 5 real Sheet Happens tabs (Still Searching, Shark Attack,
# etc.), all using exactly Target=Segno/Coda + Jump=DaCoda/DaSegnoAlCoda, one
# of each per file. Bar shape below mirrors the real file's layout: Segno(0),
# body(1), DaCoda(2), between-material(3), DaSegnoAlCoda(4), Coda(5), tail(6).


def _ds_bars(extra_at=None, extra_kwargs=None):
    """The standard 7-bar D.S. al Coda shape, with optional extra markup
    (repeat/alt) merged onto one bar by index, for the overlap tests."""
    kwargs_by_bar = {0: {}, 1: {}, 2: {}, 3: {}, 4: {}, 5: {}, 6: {}}
    if extra_at is not None:
        kwargs_by_bar[extra_at].update(extra_kwargs)
    bars = [
        _rbar(time="4/4", target="Segno", **kwargs_by_bar[0]),
        _rbar(**kwargs_by_bar[1]),
        _rbar(jump="DaCoda", **kwargs_by_bar[2]),
        _rbar(**kwargs_by_bar[3]),
        _rbar(jump="DaSegnoAlCoda", **kwargs_by_bar[4]),
        _rbar(target="Coda", **kwargs_by_bar[5]),
        _rbar(**kwargs_by_bar[6]),
    ]
    return bars


def test_plain_da_segno_al_coda():
    # First pass: 0,1,2,3,4 (DaCoda at 2 does NOT fire yet) -> D.S. jumps to 0.
    # Second pass: 0,1,2 -> DaCoda now fires -> jumps to Coda: 5,6.
    assert _order(_ds_bars()) == [0, 1, 2, 3, 4, 0, 1, 2, 5, 6]


def test_da_coda_does_not_fire_on_first_pass():
    # Regression guard: bar 3 (between DaCoda and DaSegnoAlCoda) and bar 4
    # itself must appear on the first pass — proof DaCoda didn't short-circuit
    # the trip to the D.S. jump.
    order = _order(_ds_bars())
    first_pass = order[:5]
    assert first_pass == [0, 1, 2, 3, 4]


def test_da_segno_al_coda_with_no_segno_raises():
    bars = [_rbar(time="4/4"), _rbar(jump="DaSegnoAlCoda")]
    with pytest.raises(GpifFormatError):
        _order(bars)


def test_da_coda_with_no_coda_raises():
    # DaCoda only raises once it actually tries to fire (after the D.S. jump),
    # so this needs a real Segno/DaSegnoAlCoda pair with no Target=Coda.
    bars = [
        _rbar(time="4/4", target="Segno"),
        _rbar(jump="DaCoda"),
        _rbar(jump="DaSegnoAlCoda"),
    ]
    with pytest.raises(GpifFormatError):
        _order(bars)


def test_unrecognized_direction_word_raises():
    # D.C./Fine/etc. are unconfirmed against a real file - reject cleanly
    # rather than silently mis-navigating.
    bars = [_rbar(time="4/4", target="Fine")]
    with pytest.raises(GpifFormatError):
        _order(bars)


def test_segno_bar_can_also_be_a_repeat_start():
    # Mirrors "06 Shark Attack GP.gp" bar 3: Repeat(start=true) + Target=Segno
    # on the same bar. The repeat still runs its own count independently of
    # the D.S. logic, and re-runs again on the post-D.S. pass since the D.S.
    # jump re-enters bar 0 from scratch.
    bars = _ds_bars(extra_at=0, extra_kwargs={"repeat": (True, False, 2)})
    bars[1] = _rbar(repeat=(False, True, 2))  # bar 1 closes the (0,1) repeat span
    order = _order(bars)
    assert order == [0, 1, 0, 1, 2, 3, 4, 0, 1, 0, 1, 2, 5, 6]


def test_da_coda_bar_can_also_be_second_ending():
    # Mirrors "06 Shark Attack GP.gp" bar 10: AlternateEndings=2 + Jump=DaCoda
    # on the same bar, nested inside its own repeat. The alt-ending skip on
    # pass 1 and the DaCoda-jump-on-return-trip logic must not interfere.
    bars = [
        _rbar(time="4/4", target="Segno"),
        _rbar(repeat=(True, False, 2)),
        _rbar(repeat=(False, True, 2), alt=1),
        _rbar(alt=2, jump="DaCoda"),
        _rbar(jump="DaSegnoAlCoda"),
        _rbar(target="Coda"),
        _rbar(),
    ]
    order = _order(bars)
    # First D.S. pass: 0, then repeat(1,2) takes 1st ending (bar 2) on its
    # first pass, loops, takes 2nd ending (bar 3, skipping bar 2) on its
    # second pass, then bar 4 (DaSegnoAlCoda) jumps back to Segno (bar 0).
    # Second D.S. pass: 0, then the SAME repeat span runs again from a fresh
    # pass_num=1 (D.S. re-enters bar 0 from scratch) — but a repeat always
    # takes its 1st ending on pass 1, so bar 2 is skipped only once count is
    # exhausted; here bar 3 (2nd ending) also carries DaCoda, so as soon as
    # this second D.S. pass reaches it, DaCoda fires and jumps to Coda (bar
    # 5), skipping bar 4/the D.S. jump entirely on this trip.
    assert order == [0, 1, 2, 1, 3, 4, 0, 1, 3, 5, 6]
