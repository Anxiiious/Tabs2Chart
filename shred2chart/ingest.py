"""Stage 1 — Ingest: parse a Guitar Pro file into the IR.

Supports Guitar Pro formats readable by PyGuitarPro (.gp3, .gp4, .gp5).
.gpx (Guitar Pro 6+) must be pre-converted to .gp5 externally (Route A).

PyGuitarPro 0.11 API notes:
  - Tempo lives on Song.tempo (initial BPM, int) and on
    beat.effect.mixTableChange.tempo (MixTableItem) for mid-song changes.
    MeasureHeader has NO .tempo attribute in this version.
  - NoteEffect.hammer is True for *both* hammer-on and pull-off.
  - NoteEffect.tremoloPicking (TremoloPickingEffect | None) is on NoteEffect.
  - note.realValue returns the computed MIDI pitch (tuning + fret).
  - Duration.quarterTime == 960  =>  GP_RESOLUTION = 960.

Tick arithmetic:
  PyGuitarPro uses 960 internal ticks/quarter (Duration.quarterTime = 960).
  Everything is re-scaled to 192 ticks/quarter on the way out.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import guitarpro
import guitarpro.models as gpm

from .config import Config
from .ir import IRSong, NoteEvent, SectionEvent, TempoEvent, TimeSignatureEvent

logger = logging.getLogger(__name__)

GP_RESOLUTION = 960  # guitarpro.models.Duration.quarterTime
CHART_RESOLUTION = 192


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_gp_file(path, config=None):
    """Parse a Guitar Pro file and return an IRSong.

    Parameters
    ----------
    path:
        Path to the .gp3 / .gp4 / .gp5 file.
    config:
        Optional Config supplying track selection preferences and
        custom tuning overrides.

    Returns
    -------
    IRSong
        Fully populated IR ready for Stage 2/3/4/5 processing.
    """
    if config is None:
        config = Config()

    path = Path(path)
    logger.info("Parsing %s ...", path)
    song = guitarpro.parse(str(path))

    track = _find_lead_track(song, config.track_name)
    if track is None:
        raise ValueError(
            "No guitar track found in file.  "
            "Use --track to specify the track name explicitly."
        )
    logger.info("Using track %d: %r", track.number, track.name)

    # Build tuning list from the track's string definitions.
    # track.strings is ordered high to low (string 1 = highest pitch).
    if config.tuning and len(config.tuning) == len(track.strings):
        tuning = list(config.tuning)
    else:
        tuning = [s.value for s in track.strings]

    string_count = len(track.strings)

    # Tick origin: Guitar Pro starts measure 1 at tick 960 (one quarterTime
    # before the musical downbeat).  Normalise everything relative to the
    # first measure start so bar 1 beat 1 = chart tick 0.
    first_start = song.measureHeaders[0].start

    def to_chart_tick(gp_tick):
        return round((gp_tick - first_start) * CHART_RESOLUTION / GP_RESOLUTION)

    ir = IRSong(
        title=song.title or "",
        artist=song.artist or "",
        album=getattr(song, "album", "") or "",
        resolution=CHART_RESOLUTION,
        string_count=string_count,
        tuning=tuning,
    )

    # Time-signature and section events (from measure headers).
    _extract_header_events(song.measureHeaders, ir, to_chart_tick)

    # Tempo events (initial + mid-song changes from MixTableChange).
    _extract_tempo_events(song, track, ir, to_chart_tick)

    # Notes.
    _extract_notes(track, ir, tuning, to_chart_tick)

    ir.notes.sort(key=lambda n: (n.tick, n.string))

    logger.info(
        "Parsed %d notes, %d tempo events, %d time-signature events, %d sections",
        len(ir.notes),
        len(ir.tempo_events),
        len(ir.time_signatures),
        len(ir.sections),
    )
    return ir


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_lead_track(song, track_name):
    """Return the best matching guitar track."""
    if not song.tracks:
        return None

    if track_name:
        for track in song.tracks:
            if track.name.lower() == track_name.lower():
                return track
        logger.warning("Track %r not found; falling back to heuristics.", track_name)

    # Prefer tracks whose name contains lead/guitar hints.
    hints = ["lead", "guitar", "gtr", "solo"]
    for hint in hints:
        for track in song.tracks:
            if hint in track.name.lower():
                return track

    # Fall back to the first track.
    return song.tracks[0]


def _extract_header_events(headers, ir, to_chart_tick):
    """Populate ir.time_signatures and ir.sections from measure headers."""
    prev_ts = None

    for header in headers:
        tick = to_chart_tick(header.start)

        # Time signature - only emit when it changes.
        ts = header.timeSignature
        num = ts.numerator
        denom = ts.denominator.value  # Duration object -> int (4, 8, ...)
        if (num, denom) != prev_ts:
            ir.time_signatures.append(
                TimeSignatureEvent(tick=tick, numerator=num, denominator=denom)
            )
            prev_ts = (num, denom)

        # Section / rehearsal markers.
        if header.marker is not None:
            ir.sections.append(
                SectionEvent(tick=tick, name=header.marker.title)
            )


def _extract_tempo_events(song, track, ir, to_chart_tick):
    """Populate ir.tempo_events.

    The initial tempo comes from Song.tempo.  Mid-song changes are stored
    in beat.effect.mixTableChange.tempo (a MixTableItem) on individual
    beats.  We scan the lead track's first voice to collect them.
    """
    # Initial tempo at tick 0.
    initial_bpm = float(song.tempo)
    ir.tempo_events.append(TempoEvent(tick=0, bpm=initial_bpm))

    # Collect mid-song tempo changes.
    seen_ticks = {0}
    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                if beat.start is None:
                    continue
                mtc = beat.effect.mixTableChange
                if mtc is not None and mtc.tempo is not None:
                    bpm = float(mtc.tempo.value)
                    tick = to_chart_tick(beat.start)
                    if tick not in seen_ticks:
                        ir.tempo_events.append(TempoEvent(tick=tick, bpm=bpm))
                        seen_ticks.add(tick)

    ir.tempo_events.sort(key=lambda e: e.tick)


def _extract_notes(track, ir, tuning, to_chart_tick):
    """Walk every beat in the track and append NoteEvent objects to ir.notes."""
    # Map from guitar string -> index of the last NoteEvent added for that
    # string, so we can extend it when we encounter a tie note.
    last_note_idx = {}

    chord_counter = 0

    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                if beat.start is None:
                    continue
                if not beat.notes:
                    continue

                tick = to_chart_tick(beat.start)
                beat_end = to_chart_tick(beat.start + beat.duration.time)
                duration_ticks = max(0, beat_end - tick)

                # Determine chord_id: multiple simultaneous non-tie notes.
                normal_notes = [
                    n for n in beat.notes
                    if n.type not in (gpm.NoteType.rest, gpm.NoteType.tie)
                ]
                if len(normal_notes) > 1:
                    cid = chord_counter
                    chord_counter += 1
                else:
                    cid = None

                for note in beat.notes:
                    if note.type == gpm.NoteType.rest:
                        continue

                    string_idx = note.string - 1  # 0-indexed into tuning list
                    if string_idx >= len(tuning):
                        logger.warning(
                            "Note references string %d but tuning only has %d strings; skipping.",
                            note.string, len(tuning),
                        )
                        continue

                    if note.type == gpm.NoteType.tie:
                        # Extend the previous note on this string.
                        if note.string in last_note_idx:
                            prev_idx = last_note_idx[note.string]
                            prev_note = ir.notes[prev_idx]
                            new_dur = beat_end - prev_note.tick
                            prev_note.duration_ticks = max(
                                prev_note.duration_ticks, new_dur
                            )
                        continue

                    # MIDI pitch from tuning + fret.
                    pitch = tuning[string_idx] + note.value
                    eff = note.effect

                    # In PyGuitarPro 0.11, hammer flag covers both HO and PO.
                    hopo = eff.hammer

                    # tremoloPicking lives on NoteEffect in 0.11.
                    tremolo = eff.tremoloPicking is not None

                    ne = NoteEvent(
                        tick=tick,
                        duration_ticks=duration_ticks,
                        pitch=pitch,
                        string=note.string,
                        fret=note.value,
                        chord_id=cid,
                        hammer_on=hopo,
                        pull_off=False,   # no separate flag in .gp5; covered by hammer_on
                        tap=False,        # explicit tap is .gpx-only (Route B)
                        slide_out=bool(eff.slides),
                        slide_in=False,
                        palm_mute=eff.palmMute,
                        dead_note=(note.type == gpm.NoteType.dead),
                        bend=eff.bend is not None,
                        vibrato=eff.vibrato,
                        tremolo_picked=tremolo,
                    )
                    ir.notes.append(ne)
                    last_note_idx[note.string] = len(ir.notes) - 1


# ---------------------------------------------------------------------------
# M0 validation helper
# ---------------------------------------------------------------------------

def dump_tempo_events(path):
    """Return a list of tempo-event dicts for M0 comparison.

    Scans every beat in the first track for MixTableChange tempo events.

    Usage::

        import json, shred2chart.ingest as ing
        print(json.dumps(ing.dump_tempo_events("song.gp5"), indent=2))
    """
    path = Path(path)
    song = guitarpro.parse(str(path))
    first_start = song.measureHeaders[0].start

    def to_chart_tick(gp_tick):
        return round((gp_tick - first_start) * CHART_RESOLUTION / GP_RESOLUTION)

    events = [
        {
            "measure": 1,
            "gp_tick": first_start,
            "chart_tick": 0,
            "bpm": float(song.tempo),
            "source": "song.tempo",
        }
    ]

    # Measure-level metadata (TS + markers).
    for header in song.measureHeaders:
        tick = to_chart_tick(header.start)
        entry = {
            "measure": header.number,
            "gp_tick": header.start,
            "chart_tick": tick,
            "time_sig": (
                f"{header.timeSignature.numerator}/"
                f"{header.timeSignature.denominator.value}"
            ),
            "marker": header.marker.title if header.marker is not None else None,
        }
        events.append(entry)

    # Beat-level tempo changes (first track only).
    if song.tracks:
        track = song.tracks[0]
        for measure in track.measures:
            for voice in measure.voices:
                for beat in voice.beats:
                    if beat.start is None:
                        continue
                    mtc = beat.effect.mixTableChange
                    if mtc is not None and mtc.tempo is not None:
                        tick = to_chart_tick(beat.start)
                        events.append(
                            {
                                "measure": measure.number,
                                "gp_tick": beat.start,
                                "chart_tick": tick,
                                "bpm": float(mtc.tempo.value),
                                "source": "mixTableChange",
                            }
                        )

    return events
