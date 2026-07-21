"""Section-level blending of multiple guitar tracks into one playable line.

Real Sheet Happens files split the guitar across tracks (rhythm chugs on
one, lead/taps/solos on another). A fun CH chart follows whichever part
is *interesting* at each moment, the way human charters do. v1 rule:

- Split the song at its GP section markers (Intro/Verse/Chorus/...), then
  further split each section into fixed-size sub-windows (see
  `SUB_WINDOW_BARS`) so a track switch can happen mid-section, not only
  at section boundaries. This matters for harmonized two-guitar parts
  (confirmed on a real file, "Still Searching": guitars 2 and 3 play the
  same rhythm in harmony for the whole intro section, so a section-level
  score can never tell them apart - only sub-section windows give the
  tie-break below a chance to alternate between them).
- For each window, score every candidate track: note count + a 2x
  bonus per technique flag (hammer-ons, taps, slides, bends, vibrato,
  tremolo) — so a sparse-but-flashy solo outbids a busier chug wall -
  PLUS a per-note bonus for how far above the window's other candidate
  tracks a track's average pitch sits (see `_pitch_bonus`). Raw note
  count alone systematically favors rhythm chugging over the lead: a
  palm-muted low power-chord chug is often busier (more picks per bar)
  than the higher lead/riff guitar playing over it, so count-only
  scoring picks the chug (confirmed on a real file, "Still Searching"
  section F/bar 33: guitar 1 chugs low (avg pitch ~50) with more raw
  notes than guitar 2's higher riff (avg pitch ~65), so count-only
  scoring wrongly picked the chug for the whole section - same pattern
  recurred in section E). Average pitch, not pitch range or distinct-
  pitch variety, is the signal that held up: a repetitive lead riff can
  have just as little melodic variety as a chug (both loop a short
  shape), but a rhythm guitar's chug register sits reliably below the
  lead guitar's in every section checked.
- Take the whole window from the winning track. Ties go to whichever
  track DIDN'T win the previous window (alternate for variety) - UNLESS
  the previous window was ALSO a tie between the same two tracks, in
  which case stick with the same track that just won (no alternation).
  This distinguishes two different real situations that both produce a
  tied score: genuine call-and-response (one isolated tied window between
  two otherwise-distinct parts, where alternating adds welcome variety)
  versus two guitars harmonizing continuously for a whole passage (many
  consecutive tied windows, where alternating just hops the chart between
  two tracks that are BOTH audible in the mix the entire time, producing
  a jarring, disconnected-feeling chart - confirmed on a real file,
  "Still Searching": guitars 2 and 3 play harmonized rhythm together for
  the whole intro, tying every 2-bar window for ~40 seconds straight: the
  old alternate-every-tie rule flipped tracks 4 times in the intro alone
  even though nothing about the music itself changes voice). Falls back
  to the user's priority order for the very first window or when more
  than 2 tracks are tied at once. Windows are bar-aligned so a switch
  never happens mid-riff.

The per-window choices are returned alongside the notes so the CLI can
show exactly which track "won" each window (and the user can override
by passing a single track instead).
"""
from __future__ import annotations

from typing import Any

TECHNIQUE_FLAGS = (
    "hammer_on", "pull_off", "tap", "slide_in", "slide_out",
    "bend", "vibrato", "tremolo_picked",
)
TECHNIQUE_WEIGHT = 2

# Sub-section window size, in bars (assuming 4/4 - 4 quarters/bar), for
# mid-section track switching. Bar-aligned so a switch lands on a phrase
# boundary, not mid-riff.
SUB_WINDOW_BARS = 2
SUB_WINDOW_TICKS = 960 * 4 * SUB_WINDOW_BARS


# How many extra score points one semitone of average-pitch lead over the
# window's lowest candidate is worth, per note in the track's own window.
PITCH_BONUS_PER_SEMITONE = 0.2

# A track needs at least this many notes in the window before its pitch
# bonus counts at all - guards against a single stray high note (or a
# tiny few-note phrase) outweighing a genuinely busier, established
# rhythm part on pitch alone. Below this, "not enough of a real part yet
# to second-guess note count."
PITCH_BONUS_MIN_NOTES = 8

# How much a track's raw note-count contribution gets discounted for
# being chord-heavy (fraction of its distinct attack-ticks that are
# simultaneous multi-note chords, not single notes) - a fast, repeated
# chugged chord shape racks up a much higher raw note count than a
# sparser single-note lead line playing over it without being more
# rewarding to play, so pure note count still wins even after the pitch
# bonus in a big enough count gap (confirmed on a real file, "Still
# Searching" section K/bar 67: a 100%-chord chugged riff (64 notes) beat
# a 100%-single-note lead (13 notes, +6 semitones) despite the pitch
# bonus, because a 5x count gap is too large for the bonus alone to
# close). At CHORD_DISCOUNT=0.75, a fully chord-attack track only counts
# a quarter of its notes toward the base score; a fully single-note track
# is untouched.
CHORD_DISCOUNT = 0.75


def _chordy_ratio(notes: list[dict[str, Any]]) -> float:
    """Fraction of a track's distinct attack-ticks in this window that are
    simultaneous multi-note chords rather than single notes."""
    if not notes:
        return 0.0
    tick_note_counts: dict[Any, int] = {}
    for note in notes:
        tick_note_counts[note["tick"]] = tick_note_counts.get(note["tick"], 0) + 1
    chordy = sum(1 for count in tick_note_counts.values() if count > 1)
    return chordy / len(tick_note_counts)


def _score(notes: list[dict[str, Any]]) -> float:
    score: float = len(notes) * (1 - CHORD_DISCOUNT * _chordy_ratio(notes))
    for note in notes:
        score += TECHNIQUE_WEIGHT * sum(1 for flag in TECHNIQUE_FLAGS if note.get(flag))
    return score


def _pitch_bonus(all_window_notes: list[list[dict[str, Any]]]) -> list[float]:
    """Per-track bonus rewarding a higher average pitch than the window's
    other candidates - rhythm chugging sits in a lower register than the
    lead/riff guitar playing over it, and is usually also busier, so raw
    note count alone systematically favors the chug (see module
    docstring). Tracks with no notes get 0. The lowest-average candidate
    in the window is the baseline (0 bonus); every semitone above it is
    worth PITCH_BONUS_PER_SEMITONE points per note in that track's own
    window - scaled by note count so the bonus can tip a close race
    without letting a handful of high notes outweigh a genuinely busier
    part. Tracks under PITCH_BONUS_MIN_NOTES get no bonus at all, for the
    same reason: a one-note outlier shouldn't be able to out-leverage its
    pitch gap against an established, busier part."""
    averages = []
    for notes in all_window_notes:
        pitches = [n["pitch"] for n in notes if n.get("pitch") is not None]
        averages.append(sum(pitches) / len(pitches) if pitches else None)
    real_averages = [a for a in averages if a is not None]
    if not real_averages:
        return [0.0] * len(all_window_notes)
    baseline = min(real_averages)
    return [
        (avg - baseline) * PITCH_BONUS_PER_SEMITONE * len(notes)
        if avg is not None and len(notes) >= PITCH_BONUS_MIN_NOTES
        else 0.0
        for avg, notes in zip(averages, all_window_notes)
    ]


def _sub_windows(start: int, end: float, name: str) -> list[tuple[int, float, str]]:
    """Split one section span into bar-aligned sub-windows of
    SUB_WINDOW_TICKS, so blend_tracks can switch tracks mid-section. The
    final window absorbs the remainder. `end` is always finite by the
    time this is called (blend_tracks caps the last section at the song's
    actual last note)."""
    windows = []
    pos = start
    while pos < end:
        nxt = min(pos + SUB_WINDOW_TICKS, end)
        windows.append((pos, nxt, name))
        pos = nxt
    return windows or [(start, end, name)]


def blend_tracks(
    tracks_notes: dict[int, list[dict[str, Any]]],
    priority: list[int],
    sections: list[dict[str, Any]],
    overrides: list[tuple[int, int]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge several tracks' IR note lists into one, choosing one track
    per sub-window span.

    tracks_notes: {track_id: ir_notes} (ir order as from dump_ir)
    priority: track ids in user-preference order (tie-breaker for the
        first window, and whenever more than 2 tracks tie at once)
    sections: from gpif_tempo.dump_sections (may be empty -> one span)
    overrides: optional [(tick, track_id), ...] (any order), each pinning
        every sub-window from `tick` onward (until the next override, or
        the song end) to `track_id`, bypassing scoring entirely. For the
        rare passage where the "right" track isn't the one any scoring
        heuristic - however tuned - would pick (confirmed on a real file,
        "Still Searching" section K/bar 67: the auto-blend correctly
        favors a fast chugged riff for most of the outro, but the user
        wants a manual, precise switch to a sparser noodling lead line
        partway through the section, at a point no note-count/pitch/
        chordiness signal can be tuned to isolate without breaking
        neighboring windows). Sub-windows are still bar-aligned and
        section-scored normally outside the overridden span(s).

    Returns (blended_notes, choices) where choices is
    [{"section", "start_tick", "track"}, ...] — one entry per sub-window.
    """
    # The last section (or the whole song, if there are no section markers)
    # is open-ended in principle; cap it at the last actual note so it still
    # gets split into sub-windows instead of being treated as one span.
    song_end = max(
        (n["tick"] for notes in tracks_notes.values() for n in notes), default=0
    ) + 1

    section_spans: list[tuple[int, float, str]] = []
    if not sections:
        section_spans.append((0, song_end, "(whole song)"))
    else:
        if sections[0]["tick"] > 0:
            section_spans.append((0, sections[0]["tick"], "(before first section)"))
        for i, section in enumerate(sections):
            end = sections[i + 1]["tick"] if i + 1 < len(sections) else song_end
            section_spans.append((section["tick"], end, section["name"]))

    spans: list[tuple[int, float, str]] = []
    for start, end, name in section_spans:
        spans.extend(_sub_windows(start, end, name))

    sorted_overrides = sorted(overrides) if overrides else []

    def _override_track(start: int) -> int | None:
        active = None
        for tick, track_id in sorted_overrides:
            if tick <= start:
                active = track_id
            else:
                break
        return active

    blended: list[dict[str, Any]] = []
    choices: list[dict[str, Any]] = []
    last_winner: int | None = None
    last_tied_pair: frozenset[int] | None = None
    for start, end, name in spans:
        in_span_by_track = {
            track_id: [n for n in tracks_notes[track_id] if start <= n["tick"] < end]
            for track_id in priority
        }

        override_track = _override_track(start)
        if override_track is not None and override_track in in_span_by_track:
            best_track = override_track
            last_tied_pair = None
        else:
            bonuses = _pitch_bonus([in_span_by_track[t] for t in priority])
            scores: dict[int, float] = {
                track_id: _score(in_span_by_track[track_id]) + bonus
                for track_id, bonus in zip(priority, bonuses)
            }

            best_score = max(scores.values(), default=0)
            if best_score == 0:
                continue  # no track has anything here
            tied = [t for t in priority if scores[t] == best_score]
            if len(tied) == 1:
                best_track = tied[0]
                last_tied_pair = None
            elif len(tied) == 2:
                pair = frozenset(tied)
                if last_tied_pair == pair:
                    # A run of consecutive ties between the same two tracks is
                    # one continuous harmony passage, not call-and-response -
                    # stick with whichever of them just won instead of hopping.
                    best_track = last_winner if last_winner in tied else tied[0]
                elif last_winner is not None and last_winner in tied:
                    # Isolated tie: alternate for variety.
                    best_track = next(t for t in tied if t != last_winner)
                else:
                    best_track = tied[0]
                last_tied_pair = pair
            else:
                best_track = tied[0]  # priority order fallback
                last_tied_pair = None

        blended.extend(in_span_by_track[best_track])
        choices.append({"section": name, "start_tick": start, "track": best_track})
        last_winner = best_track

    blended.sort(key=lambda n: n["tick"])
    return blended, choices
