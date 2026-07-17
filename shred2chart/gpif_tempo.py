"""Tempo/time-signature extraction directly from a GPIF XML document
(the score.gpif file inside a `.gp`/`.gpx` container — see gpx_reader.py).

This is the direct-parse path (no Guitar Pro/TuxGuitar conversion step
needed) and was written against a real Sheet Happens tab's score.gpif,
so the schema below — <MasterTrack><Automations><Automation> with
<Type>Tempo</Type>, <Bar>, <Position>, <Value>"bpm ref"</Value>, and
per-bar <MasterBar><Time>N/D</Time> — is confirmed real, not guessed.

Caveat: that reference file only had ONE tempo automation (a constant
123 bpm, at Bar 0 Position 0) — there was no example of a *mid-bar*
tempo change to confirm what a nonzero <Position> means. We assume it's
a 0..1 fraction of the bar (the common convention), but treat that as
unverified until we see a real file with a mid-bar change.

Output shape matches shred2chart.tempo.dump_tempo_events exactly, so
events from a converted .gp5 (PyGuitarPro) and events read directly from
a .gpx/.gp's score.gpif can be diffed against each other tick-for-tick.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

# Matches shred2chart.tempo.TICKS_PER_QUARTER (PyGuitarPro's own convention)
# so events from both modules line up on the same tick scale.
TICKS_PER_QUARTER = 960


class GpifFormatError(ValueError):
    """Raised when score.gpif doesn't have the structure this parser expects."""


def _bar_length_ticks(numerator: int, denominator: int) -> int:
    return numerator * TICKS_PER_QUARTER * 4 // denominator


def _parse_time_signature(master_bar: ET.Element, previous: tuple[int, int]) -> tuple[int, int]:
    time_el = master_bar.find("Time")
    if time_el is None or not time_el.text:
        return previous  # GPIF omits <Time> when it's unchanged from the previous bar
    numerator_text, _, denominator_text = time_el.text.partition("/")
    return int(numerator_text), int(denominator_text)


def dump_tempo_events(xml_text: str) -> list[dict[str, Any]]:
    """Return a tick-ordered list of tempo and time-signature events.

    Each event is one of:
      {"tick": int, "type": "tempo", "bpm": float}
      {"tick": int, "type": "time_signature", "numerator": int, "denominator": int}
    """
    root = ET.fromstring(xml_text)

    master_bars = root.findall("./MasterBars/MasterBar")
    if not master_bars:
        raise GpifFormatError("no <MasterBars><MasterBar> elements found")

    bar_starts: list[int] = []
    bar_signatures: list[tuple[int, int]] = []
    tick = 0
    last_sig = (4, 4)  # GPIF's own implicit default when the very first bar omits <Time>
    for master_bar in master_bars:
        last_sig = _parse_time_signature(master_bar, last_sig)
        bar_starts.append(tick)
        bar_signatures.append(last_sig)
        tick += _bar_length_ticks(*last_sig)

    events: list[dict[str, Any]] = []
    previous_sig: tuple[int, int] | None = None
    for i, sig in enumerate(bar_signatures):
        if sig != previous_sig:
            events.append({
                "tick": bar_starts[i],
                "type": "time_signature",
                "numerator": sig[0],
                "denominator": sig[1],
            })
            previous_sig = sig

    for automation in root.findall("./MasterTrack/Automations/Automation"):
        type_el = automation.find("Type")
        if type_el is None or type_el.text != "Tempo":
            continue
        bar_el = automation.find("Bar")
        position_el = automation.find("Position")
        value_el = automation.find("Value")
        if bar_el is None or position_el is None or value_el is None or not value_el.text:
            raise GpifFormatError("a Tempo <Automation> is missing Bar/Position/Value")

        bar = int(bar_el.text)
        position = float(position_el.text)
        bpm = float(value_el.text.split()[0])
        if bpm.is_integer():
            bpm = int(bpm)

        if not (0 <= bar < len(bar_starts)):
            raise GpifFormatError(f"Tempo automation references out-of-range bar {bar}")
        numerator, denominator = bar_signatures[bar]
        tick_pos = bar_starts[bar] + round(position * _bar_length_ticks(numerator, denominator))
        events.append({"tick": tick_pos, "type": "tempo", "bpm": bpm})

    events.sort(key=lambda e: e["tick"])
    return events
