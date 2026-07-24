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

A track counts as "lead" for a span when its technique-flag density is
meaningfully higher than the runner-up's (bends/slides/hammer-ons/taps/
vibrato — the "squiddly doo" stuff) — that track wins the whole span
outright, same as a human charter always following the solo over the
rhythm wall behind it. Two tracks only alternate bar-by-bar when they're
both similarly technique-heavy at once (a genuine twin-lead harmony,
not one lead over one chug part) and bar boundaries are supplied.

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

LEAD_DOMINANCE_RATIO = 1.5  # a track's technique weight must beat the runner-up's by this much to win outright as "the lead"
HARMONY_RATIO = 0.6  # second-best track's total score must reach this fraction of the winner's to alternate bar-by-bar


def _score(notes: list[dict[str, Any]]) -> tuple[int, int]:
    """(total_score, technique_weight) - technique_weight isolates the
    "flashy solo" signal from raw note count, so a busy chug part can't
    out-rank a sparse-but-technique-heavy lead on note count alone."""
    technique_weight = sum(
        TECHNIQUE_WEIGHT * sum(1 for flag in TECHNIQUE_FLAGS if note.get(flag))
        for note in notes
    )
    return len(notes) + technique_weight, technique_weight


def _ranked_tracks(
    tracks_notes: dict[int, list[dict[str, Any]]],
    priority: list[int],
    start: float,
    end: float,
) -> list[tuple[int, int, int]]:
    """[(track_id, score, technique_weight), ...] for this span, highest
    score first, ties broken by `priority` order. Tracks scoring 0 are
    omitted."""
    scored = []
    for track_id in priority:
        in_span = [n for n in tracks_notes[track_id] if start <= n["tick"] < end]
        span_score, technique_weight = _score(in_span)
        if span_score > 0:
            scored.append((track_id, span_score, technique_weight))
    scored.sort(key=lambda item: (-item[1], priority.index(item[0])))
    return scored


def blend_tracks(
    tracks_notes: dict[int, list[dict[str, Any]]],
    priority: list[int],
    sections: list[dict[str, Any]],
    bar_starts: list[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge several tracks' IR note lists into one, choosing one track
    per section span (or alternating bar-by-bar within a span when two
    tracks are both genuinely active there — see module docstring).

    tracks_notes: {track_id: ir_notes} (ir order as from dump_ir)
    priority: track ids in user-preference order (tie-breaker)
    sections: from gpif_tempo.dump_sections (may be empty -> one span)
    bar_starts: from gpif_tempo.compute_bar_grid, needed to detect and
        subdivide harmonized spans; without it, sections are never split.

    Returns (blended_notes, choices) where choices is
    [{"section", "start_tick", "track"}, ...] (one entry per section, or
    per alternating bar sub-range within a harmonized section).
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
        ranked = _ranked_tracks(tracks_notes, priority, start, end)
        if not ranked:
            continue  # no track has anything here

        # A clear technique leader (the "squiddly doo" part) wins the
        # whole span outright, even if a busier chug track has a higher
        # raw note count - that's the whole point of tracking technique
        # weight separately from total score.
        technique_ranked = sorted(ranked, key=lambda item: (-item[2], priority.index(item[0])))
        top_id, _, top_technique = technique_ranked[0]
        runner_technique = technique_ranked[1][2] if len(technique_ranked) >= 2 else 0
        lead_dominant = top_technique > 0 and (
            runner_technique == 0 or top_technique >= runner_technique * LEAD_DOMINANCE_RATIO
        )

        harmonized = (
            not lead_dominant
            and bar_starts
            and len(ranked) >= 2
            and ranked[1][1] >= ranked[0][1] * HARMONY_RATIO
        )

        if not harmonized:
            # Single winner for the whole span - lead-dominant track, or
            # plain highest score when no track stands out technically.
            winner = top_id if lead_dominant else ranked[0][0]
            alternates = [t for t, _, _ in ranked if t != winner][:1]
            _emit_span(
                blended, choices, tracks_notes, name, start, end, bar_starts,
                lambda bar_index, w=winner: w, alternates,
            )
            continue

        # Two tracks are both genuinely active at once (a harmonized
        # twin-guitar riff, not just a quiet doubling) - alternate
        # between them bar-by-bar so the chart reads both harmony parts
        # instead of only ever showing one.
        alternates = [t for t, _, _ in ranked[:2]]
        _emit_span(
            blended, choices, tracks_notes, name, start, end, bar_starts,
            lambda bar_index, alt=alternates: alt[bar_index % 2], alternates,
        )

    blended.sort(key=lambda n: n["tick"])
    return blended, choices


def _emit_span(
    blended: list[dict[str, Any]],
    choices: list[dict[str, Any]],
    tracks_notes: dict[int, list[dict[str, Any]]],
    name: str,
    start: float,
    end: float,
    bar_starts: list[int] | None,
    preferred_for_bar,
    fallback_order: list[int],
) -> None:
    """Emit one span's notes, bar by bar if `bar_starts` is available.

    `preferred_for_bar(bar_index)` picks the track a bar *should* use
    (the span winner, or the alternating harmony part). If that track has
    no notes in this specific bar - it hasn't entered yet, or has already
    dropped out - the bar falls back to whichever of `fallback_order`
    actually has notes there, instead of going silent. Without bar
    boundaries, the whole span is emitted from `preferred_for_bar(0)`
    with no per-bar fallback (matches pre-alternation behavior).
    """
    if not bar_starts:
        track_id = preferred_for_bar(0)
        blended.extend(n for n in tracks_notes[track_id] if start <= n["tick"] < end)
        choices.append({"section": name, "start_tick": start, "track": track_id})
        return

    span_bars = [b for b in bar_starts if start <= b < end] or [start]
    bar_bounds = span_bars + [end]
    for bar_index, bar_start in enumerate(span_bars):
        bar_end = bar_bounds[bar_index + 1]
        preferred = preferred_for_bar(bar_index)
        candidates = [preferred] + [t for t in fallback_order if t != preferred]
        for track_id in candidates:
            bar_notes = [n for n in tracks_notes[track_id] if bar_start <= n["tick"] < bar_end]
            if bar_notes:
                break
        else:
            continue  # no candidate has anything in this bar
        blended.extend(bar_notes)
        label = f"{name} (bar {bar_index + 1})" if len(span_bars) > 1 else name
        choices.append({"section": label, "start_tick": bar_start, "track": track_id})
