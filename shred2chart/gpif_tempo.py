"""Tempo/time-signature extraction directly from a GPIF XML document
(the score.gpif file inside a `.gp`/`.gpx` container — see gpx_reader.py).

This is the direct-parse path (no Guitar Pro/TuxGuitar conversion step
needed) and was written against a real Sheet Happens tab's score.gpif,
so the schema below — <MasterTrack><Automations><Automation> with
<Type>Tempo</Type>, <Bar>, <Position>, <Value>"bpm ref"</Value>, and
per-bar <MasterBar><Time>N/D</Time> — is confirmed real, not guessed.

Caveats:
- <Position>: the reference files seen so far only have Position=0 (bar
  start).  We assume a nonzero value is a 0..1 fraction of the bar (the
  common convention), but treat that as unverified until a real file with
  a mid-bar change turns up.
- <Linear>true</Linear>: indicates a gradual tempo ramp to the next
  automation point.  .chart has no ramp concept, so this is discretized
  into one stepped "B" event per beat across the ramp span (per §4 of
  the game plan).  This path has been exercised by a synthetic test
  fixture but not yet against a real GP file that carries a ramp.

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


def compute_bar_grid(root: ET.Element) -> tuple[list[int], list[tuple[int, int]]]:
    """Walk <MasterBars> in order and return, per bar index: its starting
    tick and its (numerator, denominator) time signature. Shared with
    shred2chart.ir_gpif, which needs the same per-bar tick grid to place
    notes."""
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
    return bar_starts, bar_signatures


def dump_sections(xml_text: str) -> list[dict[str, Any]]:
    """Return section markers from <MasterBar><Section><Text> elements:
    [{"tick": int, "bar": int, "name": str}, ...] in song order. Real
    Sheet Happens files carry these ("Intro", "Verse", ...) and they
    drive both .chart [Events] output and section-level track blending."""
    root = ET.fromstring(xml_text)
    bar_starts, _ = compute_bar_grid(root)
    sections = []
    for bar_index, master_bar in enumerate(root.findall("./MasterBars/MasterBar")):
        text = master_bar.findtext("./Section/Text")
        if text is not None and text.strip():
            sections.append({"tick": bar_starts[bar_index], "bar": bar_index, "name": text.strip()})
    return sections


def dump_tempo_events(xml_text: str) -> list[dict[str, Any]]:
    """Return a tick-ordered list of tempo and time-signature events.

    Each event is one of:
      {"tick": int, "type": "tempo", "bpm": float}
      {"tick": int, "type": "time_signature", "numerator": int, "denominator": int}
    """
    root = ET.fromstring(xml_text)
    bar_starts, bar_signatures = compute_bar_grid(root)

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

    # First pass: collect every Tempo automation as (tick, bpm, linear).
    raw: list[tuple[int, float, bool]] = []
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

        if not (0 <= bar < len(bar_starts)):
            raise GpifFormatError(f"Tempo automation references out-of-range bar {bar}")
        numerator, denominator = bar_signatures[bar]
        tick_pos = bar_starts[bar] + round(position * _bar_length_ticks(numerator, denominator))

        linear_el = automation.find("Linear")
        linear = (
            linear_el is not None
            and linear_el.text is not None
            and linear_el.text.strip().lower() == "true"
        )
        raw.append((tick_pos, bpm, linear))

    raw.sort(key=lambda t: t[0])

    # Second pass: emit events.  Linear automations are discretized into one
    # stepped B event per beat (TICKS_PER_QUARTER) across the ramp span,
    # interpolating bpm toward the next automation's value.  This matches
    # §4 of the game plan ("one stepped B event per beat across the ramp
    # span") and produces a .chart-compatible step-function approximation.
    for i, (tick_pos, bpm, linear) in enumerate(raw):
        if not linear:
            bpm_out = int(bpm) if float(bpm).is_integer() else bpm
            events.append({"tick": tick_pos, "type": "tempo", "bpm": bpm_out})
        else:
            # If this is the last automation, no ramp endpoint is known:
            # fall back to a single instantaneous event.
            if i + 1 >= len(raw):
                bpm_out = int(bpm) if float(bpm).is_integer() else bpm
                events.append({"tick": tick_pos, "type": "tempo", "bpm": bpm_out})
                continue
            end_tick, end_bpm, _ = raw[i + 1]
            span = end_tick - tick_pos
            n_beats = max(1, span // TICKS_PER_QUARTER)
            for beat in range(n_beats):
                t = tick_pos + beat * TICKS_PER_QUARTER
                frac = beat / n_beats
                interp = bpm + frac * (end_bpm - bpm)
                interp = round(interp, 6)
                bpm_out = int(interp) if float(interp).is_integer() else interp
                events.append({"tick": t, "type": "tempo", "bpm": bpm_out})

    events.sort(key=lambda e: e["tick"])
    return events
