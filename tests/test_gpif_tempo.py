"""Tests for direct GPIF XML tempo/time-signature extraction.

The schema asserted here (MasterTrack/Automations/Automation with
Type=Tempo, and per-bar MasterBar/Time) was confirmed against a real
Sheet Happens tab's score.gpif, not guessed — see
shred2chart/gpif_tempo.py's module docstring and
SHRED2CHART_GAMEPLAN.md's Current State.
"""
from __future__ import annotations

from shred2chart.gpif_tempo import TICKS_PER_QUARTER, dump_tempo_events

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
