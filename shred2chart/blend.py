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
rhythm wall behind it. When two similarly active tracks form a genuine
twin-guitar harmony, the blended line follows the upper voice whenever
it enters; it does not alternate whole bars between parts. Notes copied
from that upper voice carry a private ``harmony_voice`` marker so the
mapper can give the resulting chord the extra lane spread it needs.

Palm-muted/dead (ghost) notes are the opposite signal: an explicit,
reliable marker of rhythm-guitar articulation (the muted chugging
attack), not melodic playing. A track can rack up a technique bonus
from a couple of incidental hammer-ons or slides played *within* an
otherwise heavily palm-muted pattern (common in metalcore chug riffs)
without being a real lead line — so palm-mute/dead-note density is
subtracted from the technique bonus before the lead-dominance check,
preventing that false positive.

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

RHYTHM_FLAGS = ("palm_mute", "dead_note")
RHYTHM_WEIGHT = 2  # per-note penalty against lead_index, same magnitude as TECHNIQUE_WEIGHT's bonus

LEAD_DOMINANCE_RATIO = 1.5  # a track's lead_index must beat the runner-up's by this much to win outright as "the lead"
HARMONY_RATIO = 0.6  # second-best track's total score must reach this fraction of the winner's for harmony handling
# Two parts that share this many simultaneous onsets in one bar are a dense
# harmonized chord passage, not a brief call-and-response.  Retain both in a
# single playable chord representation (the mapper caps it at three lanes).
DENSE_HARMONY_ONSETS = 8


def _score(notes: list[dict[str, Any]]) -> tuple[int, int, int]:
    """(total_score, technique_weight, lead_index).

    technique_weight isolates the "flashy solo" signal from raw note
    count, so a busy chug part can't out-rank a sparse-but-technique-heavy
    lead on note count alone. lead_index nets that bonus against a
    palm-mute/dead-note penalty, so a track that's mostly muted rhythm
    playing can't pass as "the lead" just for a few embellishments
    played within the muted pattern - see module docstring.
    """
    technique_weight = sum(
        TECHNIQUE_WEIGHT * sum(1 for flag in TECHNIQUE_FLAGS if note.get(flag))
        for note in notes
    )
    rhythm_weight = sum(
        RHYTHM_WEIGHT * sum(1 for flag in RHYTHM_FLAGS if note.get(flag))
        for note in notes
    )
    return len(notes) + technique_weight, technique_weight, technique_weight - rhythm_weight


def _ranked_tracks(
    tracks_notes: dict[int, list[dict[str, Any]]],
    priority: list[int],
    start: float,
    end: float,
) -> list[tuple[int, int, int, int]]:
    """[(track_id, score, technique_weight, lead_index), ...] for this
    span, highest score first, ties broken by `priority` order. Tracks
    scoring 0 are omitted."""
    scored = []
    for track_id in priority:
        in_span = [n for n in tracks_notes[track_id] if start <= n["tick"] < end]
        span_score, technique_weight, lead_index = _score(in_span)
        if span_score > 0:
            scored.append((track_id, span_score, technique_weight, lead_index))
    scored.sort(key=lambda item: (-item[1], priority.index(item[0])))
    return scored


def blend_tracks(
    tracks_notes: dict[int, list[dict[str, Any]]],
    priority: list[int],
    sections: list[dict[str, Any]],
    bar_starts: list[int] | None = None,
    bar_track_overrides: dict[int, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge several tracks' IR note lists into one, choosing one track
    per section span (or alternating bar-by-bar within a span when two
    tracks are both genuinely active there — see module docstring).

    tracks_notes: {track_id: ir_notes} (ir order as from dump_ir)
    priority: track ids in user-preference order (tie-breaker)
    sections: from gpif_tempo.dump_sections (may be empty -> one span)
    bar_starts: from gpif_tempo.compute_bar_grid, needed to detect and
        subdivide harmonized spans; without it, sections are never split.
    bar_track_overrides: {bar_start_tick: track_id} forced selections. An
        override is strict: a selected silent track leaves that source bar
        silent rather than quietly substituting a different guitar part.

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

        # A clear lead leader (the "squiddly doo" part, net of any
        # palm-mute/dead-note penalty) wins the whole span outright, even
        # if a busier chug track has a higher raw note count - that's the
        # whole point of tracking lead_index separately from total score.
        lead_ranked = sorted(ranked, key=lambda item: (-item[3], priority.index(item[0])))
        top_id, _, _, top_lead = lead_ranked[0]
        runner_lead = lead_ranked[1][3] if len(lead_ranked) >= 2 else 0
        lead_dominant = top_lead > 0 and (
            runner_lead <= 0 or top_lead >= runner_lead * LEAD_DOMINANCE_RATIO
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
            alternates = [t for t, _, _, _ in ranked if t != winner][:1]
            _emit_span(
                blended, choices, tracks_notes, name, start, end, bar_starts,
                lambda bar_index, w=winner: w, alternates, bar_track_overrides,
            )
            continue

        # Two tracks are both genuinely active at once (a harmonized
        # twin-guitar riff, not just a quiet doubling). Follow the upper
        # register when it is present instead of arbitrarily swapping
        # whole bars between the two parts.
        _emit_harmony_span(
            blended, choices, tracks_notes, name, start, end, bar_starts,
            [t for t, _, _, _ in ranked[:2]], bar_track_overrides,
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
    bar_track_overrides: dict[int, int] | None,
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
        override = (bar_track_overrides or {}).get(bar_start)
        preferred = override if override is not None else preferred_for_bar(bar_index)
        candidates = [preferred] if override is not None else [
            preferred, *[t for t in fallback_order if t != preferred],
        ]
        for track_id in candidates:
            bar_notes = [n for n in tracks_notes[track_id] if bar_start <= n["tick"] < bar_end]
            if bar_notes:
                break
        else:
            continue  # no candidate has anything in this bar
        blended.extend(bar_notes)
        label = f"{name} (bar {bar_index + 1})" if len(span_bars) > 1 else name
        choices.append({"section": label, "start_tick": bar_start, "track": track_id})


def _median_pitch(notes: list[dict[str, Any]]) -> float:
    """A stable register representative for one track within one bar."""
    pitches = sorted(note["pitch"] for note in notes if note.get("pitch") is not None)
    if not pitches:
        return float("-inf")
    midpoint = len(pitches) // 2
    if len(pitches) % 2:
        return pitches[midpoint]
    return (pitches[midpoint - 1] + pitches[midpoint]) / 2


def _is_dense_simultaneous_harmony(active: dict[int, list[dict[str, Any]]]) -> bool:
    """Whether the active parts form a sustained, simultaneous chord wall."""
    if len(active) < 2:
        return False
    onset_sets = [set(note["tick"] for note in notes) for notes in active.values()]
    shared = set.intersection(*onset_sets)
    return len(shared) >= DENSE_HARMONY_ONSETS


def _emit_harmony_span(
    blended: list[dict[str, Any]],
    choices: list[dict[str, Any]],
    tracks_notes: dict[int, list[dict[str, Any]]],
    name: str,
    start: float,
    end: float,
    bar_starts: list[int] | None,
    candidates: list[int],
    bar_track_overrides: dict[int, int] | None,
) -> None:
    """Emit a twin-guitar span without arbitrary bar-by-bar alternation.

    The higher-register active part wins each bar.  Mark it when it is not
    the priority track, preserving the musical reason the mapper should
    widen its two-note chord shape (for example, G/R -> R/B rather than an
    unrelated G/R -> R/Y jump).
    """
    if not bar_starts:
        bar_starts = [start]
    span_bars = [bar for bar in bar_starts if start <= bar < end] or [start]
    bar_bounds = span_bars + [end]
    priority_order = {track_id: index for index, track_id in enumerate(candidates)}

    for bar_index, bar_start in enumerate(span_bars):
        bar_end = bar_bounds[bar_index + 1]
        override = (bar_track_overrides or {}).get(bar_start)
        if override is not None:
            bar_notes = [
                note for note in tracks_notes[override]
                if bar_start <= note["tick"] < bar_end
            ]
            if bar_notes:
                blended.extend(bar_notes)
            label = f"{name} (bar {bar_index + 1})" if len(span_bars) > 1 else name
            choices.append({"section": label, "start_tick": bar_start, "track": override})
            continue
        active = {
            track_id: [note for note in tracks_notes[track_id] if bar_start <= note["tick"] < bar_end]
            for track_id in candidates
        }
        active = {track_id: notes for track_id, notes in active.items() if notes}
        if not active:
            continue
        track_id, bar_notes = max(
            active.items(),
            key=lambda item: (_median_pitch(item[1]), -priority_order[item[0]]),
        )
        is_upper_harmony = track_id != candidates[0] and len(active) >= 2
        if _is_dense_simultaneous_harmony(active):
            # A long chord wall needs both guitar parts to show its changes.
            # Keep their source pitches together; mapper.py deduplicates octave
            # doubles and limits the emitted shape to three playable lanes.
            for active_track, notes in active.items():
                if active_track == track_id:
                    blended.extend([{**note, "harmony_voice": True} for note in notes])
                else:
                    blended.extend(notes)
            label = f"{name} (bar {bar_index + 1})" if len(span_bars) > 1 else name
            choices.append({"section": label, "start_tick": bar_start, "track": track_id})
            continue
        if is_upper_harmony:
            bar_notes = [{**note, "harmony_voice": True} for note in bar_notes]
        blended.extend(bar_notes)
        label = f"{name} (bar {bar_index + 1})" if len(span_bars) > 1 else name
        choices.append({"section": label, "start_tick": bar_start, "track": track_id})
