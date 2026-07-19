"""Section-level blending of multiple guitar tracks into one playable line.

Real Sheet Happens files split the guitar across tracks (rhythm chugs on
one, lead/taps/solos on another). A fun CH chart follows whichever part
is *interesting* at each moment, the way human charters do. v1 rule:

- Split the song at its GP section markers (Intro/Verse/Chorus/...).
- For each section, score every candidate track: note count + a 2x
  bonus per technique flag (hammer-ons, taps, slides, bends, vibrato,
  tremolo) — so a sparse-but-flashy solo outbids a busier chug wall.
- Take the whole section from the winning track. Ties go to whichever
  track the user listed first. Switching only at section boundaries
  keeps phrases intact — no mid-riff track jumps.

The per-section choices are returned alongside the notes so the CLI can
show exactly which track "won" each section (and the user can override
by passing a single track instead).
"""
from __future__ import annotations

from typing import Any

TECHNIQUE_FLAGS = (
    "hammer_on", "pull_off", "tap", "slide_in", "slide_out",
    "bend", "vibrato", "tremolo_picked",
)
TECHNIQUE_WEIGHT = 2


def _score(notes: list[dict[str, Any]]) -> int:
    score = len(notes)
    for note in notes:
        score += TECHNIQUE_WEIGHT * sum(1 for flag in TECHNIQUE_FLAGS if note.get(flag))
    return score


def blend_tracks(
    tracks_notes: dict[int, list[dict[str, Any]]],
    priority: list[int],
    sections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge several tracks' IR note lists into one, choosing one track
    per section span.

    tracks_notes: {track_id: ir_notes} (ir order as from dump_ir)
    priority: track ids in user-preference order (tie-breaker)
    sections: from gpif_tempo.dump_sections (may be empty -> one span)

    Returns (blended_notes, choices) where choices is
    [{"section", "start_tick", "track"}, ...].
    """
    spans: list[tuple[int, float, str]] = []
    if not sections:
        spans.append((0, float("inf"), "(whole song)"))
    else:
        if sections[0]["tick"] > 0:
            spans.append((0, sections[0]["tick"], "(before first section)"))
        for i, section in enumerate(sections):
            end = sections[i + 1]["tick"] if i + 1 < len(sections) else float("inf")
            spans.append((section["tick"], end, section["name"]))

    blended: list[dict[str, Any]] = []
    choices: list[dict[str, Any]] = []
    for start, end, name in spans:
        best_track = None
        best_score = 0
        for track_id in priority:
            in_span = [n for n in tracks_notes[track_id] if start <= n["tick"] < end]
            span_score = _score(in_span)
            if span_score > best_score:
                best_track = track_id
                best_score = span_score
        if best_track is None:
            continue  # no track has anything here
        blended.extend(n for n in tracks_notes[best_track] if start <= n["tick"] < end)
        choices.append({"section": name, "start_tick": start, "track": best_track})

    blended.sort(key=lambda n: n["tick"])
    return blended, choices
