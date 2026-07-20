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


# --- Repeat / alternate-ending playback expansion ---------------------------
#
# GPIF stores bars in written order but plays them in performance order:
# <Repeat start/end count="N"/> loops a span N times, and
# <AlternateEndings>k</AlternateEndings> restricts a bar to pass k. The schema
# here matches the real "07 Still Searching GP.gp" (Senses Fail) file.


def _rbar(*, time=None, repeat=None, alt=None, section=None):
    """A <MasterBar> with optional Time, Repeat, AlternateEndings, Section.

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
