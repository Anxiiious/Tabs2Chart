"""Per-note intermediate representation (IR) extraction directly from a
GPIF XML document — the GP7/8 `.gp` equivalent of ir_gp.py.

This is Milestone M1 from SHRED2CHART_GAMEPLAN.md, using the note schema
confirmed against two real Sheet Happens tabs (see Current State there):

  <Bars><Bar id><Voices>v0 v1 v2 v3</Voices></Bar></Bars>          (-1 = empty)
  <Voices><Voice id><Beats>b0 b1 ...</Beats></Voice></Voices>       (ids may repeat: GP
                                                                      dedupes identical beats)
  <Beats><Beat id><Rhythm ref=R/><Notes>n0 n1</Notes></Beat></Beats>  (no <Notes> = rest)
  <Rhythms><Rhythm id><NoteValue/>[<AugmentationDot count/>][<PrimaryTuplet num den/>]</Rhythm></Rhythms>
  <Notes><Note id>
    [<Vibrato>..</Vibrato>] [<LetRing/>] [<Tie origin destination/>]
    <Properties>
      <Property name="Fret"><Fret>N</Fret></Property>
      <Property name="Midi"><Number>N</Number></Property>
      <Property name="String"><String>N</String></Property>   (0-based! we add 1)
      [<Property name="HopoOrigin"|"HopoDestination"><Enable/></Property>]
      [<Property name="Slide"><Flags>N</Flags></Property>]
      [<Property name="PalmMuted"|"Muted"|"Bended"|"Tapped"><Enable/></Property>]
    </Properties>
  </Note></Notes>

Only the primary voice (slot 0) of one track is read, matching ir_gp.py's
scope. `<MasterBar><Bars>` lists one bar-id per track, in the same order
as `<MasterTrack><Tracks>`.

Confidence notes (see SHRED2CHART_GAMEPLAN.md for the fuller picture):
- palm_mute/dead_note/bend/tap/vibrato/let_ring/tied: each confirmed
  present in at least one real file.
- hammer_on/pull_off: HopoOrigin/HopoDestination tell us THAT a note is
  a hammer-on/pull-off, but not which direction. Direction is inferred
  by comparing fret numbers against the previous note in the track's
  linear sequence (lower fret = pull-off, else hammer-on) — the exact
  approach used by editor-on-fire's GP5 importer
  (github.com/raynebc/editor-on-fire src/gp_import.c), read as a
  second-opinion cross-check on this logic. Like that reference, this
  tracks "the previous note" per track rather than per string, which is
  an approximation that only really holds up for monophonic lines.
- slide: the <Slide><Flags> bitmask is now decoded into slide_in/
  slide_out (raw `slide_flags` kept too). The bit assignments (1=shift,
  2=legato, 4=slide out down, 8=slide out up, 16=slide in from below,
  32=slide in from above) come from the same editor-on-fire source,
  which documents this exact bitmask for GP5+ files; GP7's GPIF Slide
  property appears to reuse it unchanged (our one real example,
  Flags=2, decodes as a legato slide-out, which matched what the note
  looked like in context).
- tremolo_picked: a <Tremolo> element on the *beat*, not the note; we
  copy it onto every note in that beat since the IR is note-centric.
- accent: a direct <Accent>N</Accent> child of <Note> (like Vibrato),
  confirmed in real data (always N=1 so far). ir_gp.py's PyGuitarPro
  path distinguishes a heavier accent level (0x40 flag bit) that we've
  never seen a second value for here, so both just become one `accent`
  bool.
- ghost_note: PyGuitarPro's side of this exposes a real `ghostNote` flag
  (confirmed in its gp3.py/gp5.py source), but neither real GPIF file
  we've seen contains anything named "Ghost" — this song's tab may
  simply not use the technique, or GP7 calls it something else.  Based
  on GPIF's uniform `<Property name="X"><Enable/></Property>` pattern
  for all boolean note flags, the most likely property name is
  `"GhostNote"` (matching PyGuitarPro's own field name).  Implemented
  as `"GhostNote" in props`; unverified against a real GPIF file that
  actually carries a ghost note — fix the name here if it turns out to
  be something else once such a file is seen.

Note for Stage 4 (not handled here — M1's job is a faithful raw dump):
per editor-on-fire's own importer, a tied note shouldn't be treated as a
new note event at all — it extends the *previous* note's sustain
("alter the previous note's length to include the tie note"). This IR
reports every tied note as its own entry with `tied: True`, which is the
right raw data, but whatever consumes it for note-mapping should merge
tied notes into the prior note's duration rather than a separate attack.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from .gpif_tempo import TICKS_PER_QUARTER, GpifFormatError, compute_bar_grid

_NOTE_VALUE_DENOMINATOR = {
    "Whole": 1, "Half": 2, "Quarter": 4, "Eighth": 8,
    "16th": 16, "32nd": 32, "64th": 64, "128th": 128,
}

# GP5+ binary Slide bitmask (confirmed via editor-on-fire's GP importer;
# see module docstring). GPIF's <Slide><Flags> appears to reuse it.
_SLIDE_OUT_MASK = 1 | 2 | 4 | 8  # shift, legato, out-downwards, out-upwards
_SLIDE_IN_MASK = 16 | 32  # in-from-below, in-from-above


def _rhythm_ticks(rhythm_el: ET.Element) -> int:
    note_value_el = rhythm_el.find("NoteValue")
    if note_value_el is None or note_value_el.text not in _NOTE_VALUE_DENOMINATOR:
        raise GpifFormatError(f"unrecognized <NoteValue> in Rhythm id={rhythm_el.get('id')}")
    ticks = TICKS_PER_QUARTER * 4 // _NOTE_VALUE_DENOMINATOR[note_value_el.text]

    dot_el = rhythm_el.find("AugmentationDot")
    if dot_el is not None:
        count = int(dot_el.get("count", "1"))
        added = ticks
        for _ in range(count):
            added //= 2
            ticks += added

    tuplet_el = rhythm_el.find("PrimaryTuplet")
    if tuplet_el is not None:
        num = int(tuplet_el.get("num"))
        den = int(tuplet_el.get("den"))
        ticks = ticks * den // num

    return ticks


def _note_to_ir(
    note_el: ET.Element, tick: int, duration_ticks: int, chord_id: int | None, tremolo_picked: bool,
    previous_fret: int | None,
) -> dict[str, Any]:
    props = {p.get("name"): p for p in note_el.findall("./Properties/Property")}

    def prop_int(name: str, child_tag: str) -> int | None:
        prop = props.get(name)
        if prop is None:
            return None
        child = prop.find(child_tag)
        return int(child.text) if child is not None and child.text else None

    fret = prop_int("Fret", "Fret")
    pitch = prop_int("Midi", "Number")
    string = prop_int("String", "String")
    slide_flags = prop_int("Slide", "Flags") or 0
    is_hopo = "HopoDestination" in props

    return {
        "tick": tick,
        "duration_ticks": duration_ticks,
        "pitch": pitch,
        "string": string + 1 if string is not None else None,  # GPIF strings are 0-based
        "fret": fret,
        "chord_id": chord_id,
        "hammer_on": is_hopo and (previous_fret is None or fret is None or fret >= previous_fret),
        "pull_off": is_hopo and previous_fret is not None and fret is not None and fret < previous_fret,
        "slide_in": bool(slide_flags & _SLIDE_IN_MASK),
        "slide_out": bool(slide_flags & _SLIDE_OUT_MASK),
        "slide_flags": slide_flags,
        "palm_mute": "PalmMuted" in props,
        "dead_note": "Muted" in props,
        "bend": "Bended" in props,
        "tap": "Tapped" in props,
        "vibrato": note_el.find("Vibrato") is not None,
        "let_ring": note_el.find("LetRing") is not None,
        "tied": note_el.find("Tie") is not None,
        "tremolo_picked": tremolo_picked,
        "accent": note_el.find("Accent") is not None,
        "ghost_note": "GhostNote" in props,  # property name inferred from GPIF pattern; unverified against a real ghost-note file
    }


def list_tracks(xml_text: str) -> list[tuple[int, str]]:
    """Return [(track_id, name), ...] so a caller can pick the right one
    for `dump_ir` — e.g. one real file has separate "Rhythm Guitar" and
    "Lead Guitar" tracks with very different technique markings; another
    has three identically-named "Overdriven Guitar" tracks. Don't assume
    track 0 is the one you want."""
    root = ET.fromstring(xml_text)
    tracks = []
    for track_el in root.findall("./Tracks/Track"):
        name_el = track_el.find("Name")
        tracks.append((int(track_el.get("id")), name_el.text if name_el is not None else ""))
    return tracks


def dump_ir(xml_text: str, track_index: int = 0) -> list[dict[str, Any]]:
    """Return a tick-ordered list of note IR dicts for one track's
    primary voice. `track_index` matches the `<Track id>` in the GPIF
    (0-based, in file order)."""
    root = ET.fromstring(xml_text)
    bar_starts, _ = compute_bar_grid(root)

    tracks_el = root.find("./MasterTrack/Tracks")
    if tracks_el is None or not tracks_el.text:
        raise GpifFormatError("no <MasterTrack><Tracks> element found")
    track_ids = [int(x) for x in tracks_el.text.split()]
    if track_index not in track_ids:
        raise ValueError(f"track_index {track_index} not found among tracks {track_ids}")
    position = track_ids.index(track_index)

    bars_by_id = {int(b.get("id")): b for b in root.find("./Bars").findall("Bar")}
    voices_by_id = {int(v.get("id")): v for v in root.find("./Voices").findall("Voice")}
    beats_by_id = {int(b.get("id")): b for b in root.find("./Beats").findall("Beat")}
    notes_by_id = {int(n.get("id")): n for n in root.find("./Notes").findall("Note")}
    rhythms_by_id = {int(r.get("id")): r for r in root.find("./Rhythms").findall("Rhythm")}

    master_bars = root.findall("./MasterBars/MasterBar")
    notes_ir: list[dict[str, Any]] = []
    chord_counter = 0
    previous_fret: int | None = None

    for bar_index, master_bar in enumerate(master_bars):
        bars_ref_el = master_bar.find("Bars")
        if bars_ref_el is None or not bars_ref_el.text:
            raise GpifFormatError(f"MasterBar {bar_index} has no <Bars> reference list")
        bar_ids = [int(x) for x in bars_ref_el.text.split()]
        bar_el = bars_by_id[bar_ids[position]]

        voices_ref_el = bar_el.find("Voices")
        voice_ids = [int(x) for x in voices_ref_el.text.split()] if voices_ref_el is not None and voices_ref_el.text else []
        voice_id = voice_ids[0] if voice_ids else -1
        if voice_id == -1:
            continue  # this track has nothing in this bar (rest measure)

        beats_ref_el = voices_by_id[voice_id].find("Beats")
        beat_ids = [int(x) for x in beats_ref_el.text.split()] if beats_ref_el is not None and beats_ref_el.text else []

        tick = bar_starts[bar_index]
        for beat_id in beat_ids:
            beat_el = beats_by_id[beat_id]
            rhythm_ref_el = beat_el.find("Rhythm")
            if rhythm_ref_el is None:
                raise GpifFormatError(f"Beat id={beat_id} has no <Rhythm> reference")
            duration_ticks = _rhythm_ticks(rhythms_by_id[int(rhythm_ref_el.get("ref"))])

            notes_ref_el = beat_el.find("Notes")
            if notes_ref_el is not None and notes_ref_el.text:
                note_ids = [int(x) for x in notes_ref_el.text.split()]
                chord_id = None
                if len(note_ids) > 1:
                    chord_id = chord_counter
                    chord_counter += 1
                tremolo_picked = beat_el.find("Tremolo") is not None
                for note_id in note_ids:
                    note_el = notes_by_id[note_id]
                    ir_note = _note_to_ir(note_el, tick, duration_ticks, chord_id, tremolo_picked, previous_fret)
                    notes_ir.append(ir_note)
                    if ir_note["fret"] is not None:
                        previous_fret = ir_note["fret"]

            tick += duration_ticks

    return notes_ir
