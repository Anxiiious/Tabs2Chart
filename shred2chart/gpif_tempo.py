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


def _alternate_ending(master_bar: ET.Element) -> int | None:
    """The 1-based pass number a bar's <AlternateEndings> restricts it to,
    or None if the bar is always played. GP marks the 1st ending bar with
    `<AlternateEndings>1</AlternateEndings>` and the 2nd with `2`."""
    el = master_bar.find("AlternateEndings")
    if el is None or not el.text or not el.text.strip():
        return None
    return int(el.text.strip())


def compute_playback_order(root: ET.Element) -> list[int]:
    """Return file-order <MasterBar> indices in *performance* order.

    Guitar Pro stores bars in written (score) order but plays them in
    performance order: bars inside repeat barlines play N times, and
    1st/2nd endings are taken conditionally. Charts built from written
    order drift progressively out of sync with the recording, since a
    section written once but played twice occupies half the audio time it
    should. This walks the score simulating GP's repeat playback so the
    tick grid downstream reflects what's actually heard.

    Scope: <Repeat> barlines and <AlternateEndings> (1st/2nd endings).
    <Directions> jumps (D.S./D.C./Coda) are a separate navigation layer
    not handled yet — see SHRED2CHART_GAMEPLAN.md; this is the extension
    point for them.
    """
    master_bars = root.findall("./MasterBars/MasterBar")
    if not master_bars:
        raise GpifFormatError("no <MasterBars><MasterBar> elements found")

    # Precompute, per file-order bar: its repeat flags/count and alt-ending.
    repeats = []
    for mb in master_bars:
        rep = mb.find("Repeat")
        start = rep is not None and rep.get("start") == "true"
        end = rep is not None and rep.get("end") == "true"
        count = int(rep.get("count", "2")) if rep is not None else 0
        repeats.append((start, end, count, _alternate_ending(mb)))

    order: list[int] = []
    loop_back: int | None = None  # active span's repeat-start bar, or None
    pass_num = 1                  # which pass through the active span we're on
    # Loose upper bound on emitted bars, to catch malformed repeats instead
    # of looping forever: every bar, times the largest repeat count, plus slack.
    max_bars = len(master_bars) * (max((r[2] for r in repeats), default=1) + 1) + 1

    i = 0
    while i < len(master_bars):
        start, end, count, alt = repeats[i]
        # A repeat-start opens a new span. Only treat it as fresh when we're not
        # already looping within it (loop_back == i means we jumped back here,
        # which must NOT reset the pass counter or the loop never terminates).
        if start and loop_back != i:
            loop_back = i
            pass_num = 1

        # Alternate-ending bars are only played on their designated pass.
        if alt is not None and alt != pass_num:
            i += 1
            continue

        order.append(i)
        if len(order) > max_bars:
            raise GpifFormatError("repeat expansion exceeded bar cap (malformed repeats?)")

        if end and pass_num < count:
            if loop_back is None:  # repeat-end with no matching start
                raise GpifFormatError(f"repeat-end at bar {i} has no matching repeat-start")
            pass_num += 1
            i = loop_back
            continue

        if end:
            loop_back = None  # span fully consumed; next start opens a fresh one
        i += 1

    return order


def compute_bar_grid(
    root: ET.Element,
) -> tuple[list[int], list[tuple[int, int]], list[int]]:
    """Return, per *play position* (performance order, see
    compute_playback_order): its starting tick, (numerator, denominator)
    time signature, and the file-order <MasterBar> index it came from.

    A bar inside a repeat appears once per pass, at distinct ticks. Shared
    with shred2chart.ir_gpif, which needs the same tick grid to place
    notes on the played timeline."""
    master_bars = root.findall("./MasterBars/MasterBar")
    if not master_bars:
        raise GpifFormatError("no <MasterBars><MasterBar> elements found")

    # Time signatures are carried in written order (GPIF omits <Time> when
    # unchanged), so resolve each file-order bar's signature first, then
    # index by play order.
    file_signatures: list[tuple[int, int]] = []
    last_sig = (4, 4)  # GPIF's own implicit default when the very first bar omits <Time>
    for master_bar in master_bars:
        last_sig = _parse_time_signature(master_bar, last_sig)
        file_signatures.append(last_sig)

    bar_starts: list[int] = []
    bar_signatures: list[tuple[int, int]] = []
    bar_source_index: list[int] = []
    tick = 0
    for src in compute_playback_order(root):
        sig = file_signatures[src]
        bar_starts.append(tick)
        bar_signatures.append(sig)
        bar_source_index.append(src)
        tick += _bar_length_ticks(*sig)
    return bar_starts, bar_signatures, bar_source_index


def dump_sections(xml_text: str) -> list[dict[str, Any]]:
    """Return section markers from <MasterBar><Section><Text> elements:
    [{"tick": int, "bar": int, "name": str}, ...] in song order. Real
    Sheet Happens files carry these ("Intro", "Verse", ...) and they
    drive both .chart [Events] output and section-level track blending."""
    root = ET.fromstring(xml_text)
    bar_starts, _, bar_source_index = compute_bar_grid(root)
    master_bars = root.findall("./MasterBars/MasterBar")

    # First play position at which each file-order bar is heard, so a
    # section that sits on a repeated bar is marked once (at its first pass).
    first_play_pos: dict[int, int] = {}
    for play_pos, src in enumerate(bar_source_index):
        first_play_pos.setdefault(src, play_pos)

    sections = []
    for bar_index, master_bar in enumerate(master_bars):
        text = master_bar.findtext("./Section/Text")
        if text is not None and text.strip() and bar_index in first_play_pos:
            tick = bar_starts[first_play_pos[bar_index]]
            sections.append({"tick": tick, "bar": bar_index, "name": text.strip()})
    sections.sort(key=lambda s: s["tick"])
    return sections


def dump_tempo_events(xml_text: str) -> list[dict[str, Any]]:
    """Return a tick-ordered list of tempo and time-signature events.

    Each event is one of:
      {"tick": int, "type": "tempo", "bpm": float}
      {"tick": int, "type": "time_signature", "numerator": int, "denominator": int}
    """
    root = ET.fromstring(xml_text)
    bar_starts, bar_signatures, bar_source_index = compute_bar_grid(root)

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

    # Automations are addressed by file-order <Bar>; a bar inside a repeat
    # is played at several ticks, so re-emit the tempo at each of them.
    play_positions_of: dict[int, list[int]] = {}
    for play_pos, src in enumerate(bar_source_index):
        play_positions_of.setdefault(src, []).append(play_pos)

    file_bar_count = len(root.findall("./MasterBars/MasterBar"))
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

        if not (0 <= bar < file_bar_count):
            raise GpifFormatError(f"Tempo automation references out-of-range bar {bar}")
        for play_pos in play_positions_of.get(bar, []):
            numerator, denominator = bar_signatures[play_pos]
            tick_pos = bar_starts[play_pos] + round(position * _bar_length_ticks(numerator, denominator))
            events.append({"tick": tick_pos, "type": "tempo", "bpm": bpm})

    events.sort(key=lambda e: e["tick"])
    return events
