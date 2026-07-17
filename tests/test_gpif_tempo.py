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
